"""Tests for mobile authentication middleware.

Covers:
- MobileAuthContext model creation, frozen semantics, is_elevated property
- AuthenticationError exception with custom codes
- MobileAuthenticator.authenticate() success and all error paths
- create_auth_dependency() FastAPI dependency factory and inner async function
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure elle.mobile.crypto is importable even when 'cryptography' is absent.
# The real module imports 'cryptography' at the top level, which may not be
# installed in the test environment.  We inject a lightweight mock module
# into sys.modules so that ``patch("elle.mobile.crypto.MobileCrypto")`` can
# resolve the dotted path.  The patch decorator then replaces MobileCrypto
# with a per-test MagicMock as usual.
# ---------------------------------------------------------------------------
import elle.mobile

if "elle.mobile.crypto" not in sys.modules:
    _crypto_stub = MagicMock()
    sys.modules["elle.mobile.crypto"] = _crypto_stub
    elle.mobile.crypto = _crypto_stub  # Required for patch() dotted-path resolution

from elle.mobile.auth import (
    AuthenticationError,
    MobileAuthContext,
    MobileAuthenticator,
    create_auth_dependency,
)
from elle.mobile.models import DeviceStatus, Elevation, MobileRole, PairedDevice

# =============================================================================
# Mock helpers
# =============================================================================

FAKE_CERT_PEM = b"-----BEGIN CERTIFICATE-----\nFAKECERT\n-----END CERTIFICATE-----\n"
FAKE_FINGERPRINT = "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99"
CLIENT_IP = "192.168.1.42"


def _make_paired_device(
    device_id: str = "dev-001",
    name: str = "Test Phone",
    role: MobileRole = MobileRole.MOBILE_READONLY,
    status: DeviceStatus = DeviceStatus.PAIRED,
) -> PairedDevice:
    """Build a PairedDevice with sensible defaults."""
    return PairedDevice(
        device_id=device_id,
        name=name,
        role=role,
        status=status,
        cert_fingerprint=FAKE_FINGERPRINT,
        paired_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
    )


def _make_elevation(
    device_id: str = "dev-001",
    elevated_role: MobileRole = MobileRole.MOBILE_OPERATOR,
    active: bool = True,
) -> Elevation:
    """Build an Elevation that is either active or expired."""
    if active:
        expires_at = datetime.utcnow() + timedelta(minutes=10)
    else:
        expires_at = datetime.utcnow() - timedelta(minutes=10)
    return Elevation(
        device_id=device_id,
        elevated_role=elevated_role,
        expires_at=expires_at,
        granted_by="cli",
    )


def _build_authenticator(
    config: MagicMock | None = None,
    store: MagicMock | None = None,
    crypto: MagicMock | None = None,
) -> MobileAuthenticator:
    """Build a MobileAuthenticator with all dependencies mocked."""
    return MobileAuthenticator(
        config=config or MagicMock(),
        store=store or MagicMock(),
        crypto=crypto or MagicMock(),
    )


# =============================================================================
# TestMobileAuthContext
# =============================================================================


class TestMobileAuthContext:
    """Tests for the MobileAuthContext Pydantic model."""

    def test_create_context_with_required_fields(self):
        """Creating a context with all required fields succeeds."""
        ctx = MobileAuthContext(
            device_id="dev-001",
            device_name="My Phone",
            role=MobileRole.MOBILE_READONLY,
            effective_role=MobileRole.MOBILE_READONLY,
            client_ip=CLIENT_IP,
            cert_fingerprint=FAKE_FINGERPRINT,
        )
        assert ctx.device_id == "dev-001"
        assert ctx.device_name == "My Phone"
        assert ctx.role == MobileRole.MOBILE_READONLY
        assert ctx.effective_role == MobileRole.MOBILE_READONLY
        assert ctx.elevation_expires_at is None
        assert ctx.client_ip == CLIENT_IP
        assert ctx.cert_fingerprint == FAKE_FINGERPRINT

    def test_is_elevated_false_when_roles_match(self):
        """is_elevated returns False when role == effective_role."""
        ctx = MobileAuthContext(
            device_id="dev-001",
            device_name="My Phone",
            role=MobileRole.MOBILE_READONLY,
            effective_role=MobileRole.MOBILE_READONLY,
            client_ip=CLIENT_IP,
            cert_fingerprint=FAKE_FINGERPRINT,
        )
        assert ctx.is_elevated is False

    def test_is_elevated_true_when_roles_differ(self):
        """is_elevated returns True when effective_role differs from role."""
        ctx = MobileAuthContext(
            device_id="dev-001",
            device_name="My Phone",
            role=MobileRole.MOBILE_READONLY,
            effective_role=MobileRole.MOBILE_OPERATOR,
            elevation_expires_at=datetime.utcnow() + timedelta(minutes=10),
            client_ip=CLIENT_IP,
            cert_fingerprint=FAKE_FINGERPRINT,
        )
        assert ctx.is_elevated is True

    def test_model_is_frozen(self):
        """MobileAuthContext is immutable (frozen=True)."""
        from pydantic import ValidationError

        ctx = MobileAuthContext(
            device_id="dev-001",
            device_name="My Phone",
            role=MobileRole.MOBILE_READONLY,
            effective_role=MobileRole.MOBILE_READONLY,
            client_ip=CLIENT_IP,
            cert_fingerprint=FAKE_FINGERPRINT,
        )
        with pytest.raises(ValidationError):
            ctx.device_id = "changed"


# =============================================================================
# TestAuthenticationError
# =============================================================================


class TestAuthenticationError:
    """Tests for the AuthenticationError exception."""

    def test_default_code(self):
        """AuthenticationError defaults to AUTH_FAILED code."""
        err = AuthenticationError("Something went wrong")
        assert str(err) == "Something went wrong"
        assert err.code == "AUTH_FAILED"

    def test_custom_code(self):
        """AuthenticationError accepts a custom error code."""
        err = AuthenticationError("cert missing", code="CERT_REQUIRED")
        assert err.code == "CERT_REQUIRED"
        assert str(err) == "cert missing"


# =============================================================================
# TestMobileAuthenticator
# =============================================================================


class TestMobileAuthenticator:
    """Tests for MobileAuthenticator.authenticate()."""

    def test_no_cert_raises_cert_required(self):
        """Passing None as client_cert_pem raises CERT_REQUIRED."""
        auth = _build_authenticator()

        with pytest.raises(AuthenticationError) as exc_info:
            auth.authenticate(None, CLIENT_IP)

        assert exc_info.value.code == "CERT_REQUIRED"
        assert "Client certificate required" in str(exc_info.value)

    def test_invalid_cert_raises_cert_invalid(self):
        """A certificate that fails CA verification raises CERT_INVALID."""
        mock_crypto = MagicMock()
        mock_crypto.verify_client_cert.return_value = (False, "Certificate expired")
        auth = _build_authenticator(crypto=mock_crypto)

        with pytest.raises(AuthenticationError) as exc_info:
            auth.authenticate(FAKE_CERT_PEM, CLIENT_IP)

        assert exc_info.value.code == "CERT_INVALID"
        assert "Certificate expired" in str(exc_info.value)

    def test_cert_valid_but_no_device_id_raises_cert_invalid(self):
        """If verify returns valid=True but result is None, raises CERT_INVALID."""
        mock_crypto = MagicMock()
        mock_crypto.verify_client_cert.return_value = (True, None)
        auth = _build_authenticator(crypto=mock_crypto)

        with pytest.raises(AuthenticationError) as exc_info:
            auth.authenticate(FAKE_CERT_PEM, CLIENT_IP)

        assert exc_info.value.code == "CERT_INVALID"
        assert "no device ID" in str(exc_info.value)

    def test_unknown_device_raises_device_unknown(self):
        """A valid cert for an unregistered device raises DEVICE_UNKNOWN."""
        mock_crypto = MagicMock()
        mock_crypto.verify_client_cert.return_value = (True, "dev-999")
        mock_store = MagicMock()
        mock_store.get_device.return_value = None
        auth = _build_authenticator(crypto=mock_crypto, store=mock_store)

        with pytest.raises(AuthenticationError) as exc_info:
            auth.authenticate(FAKE_CERT_PEM, CLIENT_IP)

        assert exc_info.value.code == "DEVICE_UNKNOWN"
        assert "not registered" in str(exc_info.value)
        mock_store.get_device.assert_called_once_with("dev-999")

    def test_revoked_device_raises_device_revoked(self):
        """A revoked device raises DEVICE_REVOKED."""
        mock_crypto = MagicMock()
        mock_crypto.verify_client_cert.return_value = (True, "dev-001")
        device = _make_paired_device(status=DeviceStatus.REVOKED)
        mock_store = MagicMock()
        mock_store.get_device.return_value = device
        auth = _build_authenticator(crypto=mock_crypto, store=mock_store)

        with pytest.raises(AuthenticationError) as exc_info:
            auth.authenticate(FAKE_CERT_PEM, CLIENT_IP)

        assert exc_info.value.code == "DEVICE_REVOKED"
        assert "revoked" in str(exc_info.value)

    def test_pending_device_raises_status_invalid(self):
        """A device with PENDING status raises DEVICE_STATUS_INVALID."""
        mock_crypto = MagicMock()
        mock_crypto.verify_client_cert.return_value = (True, "dev-001")
        device = _make_paired_device(status=DeviceStatus.PENDING)
        mock_store = MagicMock()
        mock_store.get_device.return_value = device
        auth = _build_authenticator(crypto=mock_crypto, store=mock_store)

        with pytest.raises(AuthenticationError) as exc_info:
            auth.authenticate(FAKE_CERT_PEM, CLIENT_IP)

        assert exc_info.value.code == "DEVICE_STATUS_INVALID"
        assert "pending" in str(exc_info.value).lower()

    def test_successful_auth_returns_context(self):
        """A valid cert + paired device returns correct MobileAuthContext."""
        mock_crypto = MagicMock()
        mock_crypto.verify_client_cert.return_value = (True, "dev-001")
        mock_crypto.compute_fingerprint.return_value = FAKE_FINGERPRINT

        device = _make_paired_device()
        mock_store = MagicMock()
        mock_store.get_device.return_value = device
        mock_store.get_active_elevation.return_value = None

        auth = _build_authenticator(crypto=mock_crypto, store=mock_store)
        ctx = auth.authenticate(FAKE_CERT_PEM, CLIENT_IP)

        assert isinstance(ctx, MobileAuthContext)
        assert ctx.device_id == "dev-001"
        assert ctx.device_name == "Test Phone"
        assert ctx.role == MobileRole.MOBILE_READONLY
        assert ctx.effective_role == MobileRole.MOBILE_READONLY
        assert ctx.elevation_expires_at is None
        assert ctx.client_ip == CLIENT_IP
        assert ctx.cert_fingerprint == FAKE_FINGERPRINT
        assert ctx.is_elevated is False

    def test_successful_auth_updates_last_seen(self):
        """Successful authentication calls update_device with last_seen_at."""
        mock_crypto = MagicMock()
        mock_crypto.verify_client_cert.return_value = (True, "dev-001")
        mock_crypto.compute_fingerprint.return_value = FAKE_FINGERPRINT

        device = _make_paired_device()
        mock_store = MagicMock()
        mock_store.get_device.return_value = device
        mock_store.get_active_elevation.return_value = None

        auth = _build_authenticator(crypto=mock_crypto, store=mock_store)
        auth.authenticate(FAKE_CERT_PEM, CLIENT_IP)

        mock_store.update_device.assert_called_once()
        call_kwargs = mock_store.update_device.call_args
        assert call_kwargs[0][0] == "dev-001"
        assert "last_seen_at" in call_kwargs[1]
        assert isinstance(call_kwargs[1]["last_seen_at"], datetime)

    def test_active_elevation_promotes_effective_role(self):
        """Active elevation changes effective_role and sets expiration."""
        mock_crypto = MagicMock()
        mock_crypto.verify_client_cert.return_value = (True, "dev-001")
        mock_crypto.compute_fingerprint.return_value = FAKE_FINGERPRINT

        device = _make_paired_device(role=MobileRole.MOBILE_READONLY)
        elevation = _make_elevation(active=True)

        mock_store = MagicMock()
        mock_store.get_device.return_value = device
        mock_store.get_active_elevation.return_value = elevation

        auth = _build_authenticator(crypto=mock_crypto, store=mock_store)
        ctx = auth.authenticate(FAKE_CERT_PEM, CLIENT_IP)

        assert ctx.role == MobileRole.MOBILE_READONLY
        assert ctx.effective_role == MobileRole.MOBILE_OPERATOR
        assert ctx.elevation_expires_at == elevation.expires_at
        assert ctx.is_elevated is True

    def test_expired_elevation_keeps_base_role(self):
        """Expired (inactive) elevation does not promote role."""
        mock_crypto = MagicMock()
        mock_crypto.verify_client_cert.return_value = (True, "dev-001")
        mock_crypto.compute_fingerprint.return_value = FAKE_FINGERPRINT

        device = _make_paired_device(role=MobileRole.MOBILE_READONLY)
        elevation = _make_elevation(active=False)

        mock_store = MagicMock()
        mock_store.get_device.return_value = device
        mock_store.get_active_elevation.return_value = elevation

        auth = _build_authenticator(crypto=mock_crypto, store=mock_store)
        ctx = auth.authenticate(FAKE_CERT_PEM, CLIENT_IP)

        assert ctx.effective_role == MobileRole.MOBILE_READONLY
        assert ctx.elevation_expires_at is None
        assert ctx.is_elevated is False

    def test_no_elevation_record_keeps_base_role(self):
        """When store returns None for elevation, base role is preserved."""
        mock_crypto = MagicMock()
        mock_crypto.verify_client_cert.return_value = (True, "dev-001")
        mock_crypto.compute_fingerprint.return_value = FAKE_FINGERPRINT

        device = _make_paired_device(role=MobileRole.MOBILE_OPERATOR)
        mock_store = MagicMock()
        mock_store.get_device.return_value = device
        mock_store.get_active_elevation.return_value = None

        auth = _build_authenticator(crypto=mock_crypto, store=mock_store)
        ctx = auth.authenticate(FAKE_CERT_PEM, CLIENT_IP)

        assert ctx.role == MobileRole.MOBILE_OPERATOR
        assert ctx.effective_role == MobileRole.MOBILE_OPERATOR
        assert ctx.is_elevated is False

    def test_operator_device_no_elevation(self):
        """An operator-role device without elevation returns operator effective_role."""
        mock_crypto = MagicMock()
        mock_crypto.verify_client_cert.return_value = (True, "dev-002")
        mock_crypto.compute_fingerprint.return_value = FAKE_FINGERPRINT

        device = _make_paired_device(
            device_id="dev-002",
            name="Admin Tablet",
            role=MobileRole.MOBILE_OPERATOR,
        )
        mock_store = MagicMock()
        mock_store.get_device.return_value = device
        mock_store.get_active_elevation.return_value = None

        auth = _build_authenticator(crypto=mock_crypto, store=mock_store)
        ctx = auth.authenticate(FAKE_CERT_PEM, CLIENT_IP)

        assert ctx.device_id == "dev-002"
        assert ctx.device_name == "Admin Tablet"
        assert ctx.role == MobileRole.MOBILE_OPERATOR
        assert ctx.effective_role == MobileRole.MOBILE_OPERATOR


# =============================================================================
# TestMobileAuthenticatorInit
# =============================================================================


class TestMobileAuthenticatorInit:
    """Tests for MobileAuthenticator __init__ default injection."""

    @patch("elle.mobile.auth.MobileCrypto")
    @patch("elle.mobile.auth.MobileStore")
    @patch("elle.mobile.auth.get_mobile_config")
    def test_defaults_injected_when_none(self, mock_get_config, mock_store_cls, mock_crypto_cls):
        """When config/store/crypto are None, defaults are created from global config."""
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config

        auth = MobileAuthenticator()

        mock_get_config.assert_called_once()
        assert auth.config is mock_config
        mock_store_cls.assert_called_once_with(mock_config)
        mock_crypto_cls.assert_called_once_with(mock_config)

    def test_explicit_dependencies_used(self):
        """When config/store/crypto are passed, they are used directly."""
        config = MagicMock()
        store = MagicMock()
        crypto = MagicMock()

        auth = MobileAuthenticator(config=config, store=store, crypto=crypto)

        assert auth.config is config
        assert auth.store is store
        assert auth.crypto is crypto


# =============================================================================
# TestCreateAuthDependency
# =============================================================================


class TestCreateAuthDependency:
    """Tests for create_auth_dependency() and the inner get_auth_context()."""

    def test_returns_callable(self):
        """create_auth_dependency returns an async callable."""
        dep = create_auth_dependency(
            config=MagicMock(),
            store=MagicMock(),
            crypto=MagicMock(),
        )
        assert callable(dep)

    @pytest.mark.asyncio
    async def test_no_transport_calls_authenticate_with_none_cert(self):
        """When request has no transport, client_cert_pem is None."""
        mock_crypto = MagicMock()
        mock_store = MagicMock()

        dep = create_auth_dependency(
            config=MagicMock(),
            store=mock_store,
            crypto=mock_crypto,
        )

        # Build a minimal request mock with scope but no transport
        request = MagicMock()
        request.scope = {}
        request.client = MagicMock()
        request.client.host = "10.0.0.1"

        # authenticate will be called with None cert -> raises CERT_REQUIRED
        with pytest.raises(AuthenticationError) as exc_info:
            await dep(request)

        assert exc_info.value.code == "CERT_REQUIRED"

    @pytest.mark.asyncio
    async def test_client_ip_extracted_from_request(self):
        """Client IP is extracted from request.client.host."""
        mock_crypto = MagicMock()
        mock_crypto.verify_client_cert.return_value = (True, "dev-001")
        mock_crypto.compute_fingerprint.return_value = FAKE_FINGERPRINT

        device = _make_paired_device()
        mock_store = MagicMock()
        mock_store.get_device.return_value = device
        mock_store.get_active_elevation.return_value = None

        dep = create_auth_dependency(
            config=MagicMock(),
            store=mock_store,
            crypto=mock_crypto,
        )

        # Build a request with transport containing ssl_object + peer cert.
        # The inner function does ``from cryptography import x509`` and
        # ``from cryptography.hazmat.primitives import serialization``
        # locally, so we inject mocks into sys.modules for the duration
        # of the call.
        mock_cert_obj = MagicMock()
        mock_cert_obj.public_bytes.return_value = FAKE_CERT_PEM

        mock_x509 = MagicMock()
        mock_x509.load_der_x509_certificate.return_value = mock_cert_obj

        mock_serialization = MagicMock()
        mock_serialization.Encoding.PEM = "PEM"

        mock_ssl_obj = MagicMock()
        mock_ssl_obj.getpeercert.return_value = b"\x00DER_BYTES"

        mock_transport = MagicMock()
        mock_transport.get_extra_info.return_value = mock_ssl_obj

        request = MagicMock()
        request.scope = {"transport": mock_transport}
        request.client = MagicMock()
        request.client.host = "192.168.1.100"

        # Patch the cryptography sub-modules in sys.modules so the local
        # ``from cryptography import x509`` inside get_auth_context resolves
        # to our mocks.
        crypto_pkg = MagicMock()
        crypto_pkg.x509 = mock_x509
        hazmat = MagicMock()
        hazmat.primitives.serialization = mock_serialization

        saved_modules = {}
        modules_to_patch = {
            "cryptography": crypto_pkg,
            "cryptography.x509": mock_x509,
            "cryptography.hazmat": hazmat,
            "cryptography.hazmat.primitives": hazmat.primitives,
            "cryptography.hazmat.primitives.serialization": mock_serialization,
        }
        for mod_name, mod in modules_to_patch.items():
            saved_modules[mod_name] = sys.modules.get(mod_name)
            sys.modules[mod_name] = mod

        try:
            ctx = await dep(request)
        finally:
            for mod_name, original in saved_modules.items():
                if original is None:
                    sys.modules.pop(mod_name, None)
                else:
                    sys.modules[mod_name] = original

        assert ctx.client_ip == "192.168.1.100"
        assert isinstance(ctx, MobileAuthContext)

    @pytest.mark.asyncio
    async def test_client_ip_unknown_when_no_client(self):
        """Client IP defaults to 'unknown' when request.client is None."""
        mock_crypto = MagicMock()
        mock_store = MagicMock()

        dep = create_auth_dependency(
            config=MagicMock(),
            store=mock_store,
            crypto=mock_crypto,
        )

        # Build a request without client attribute
        request = MagicMock(spec=[])  # spec=[] means no attributes at all

        # Should call authenticate with None cert and "unknown" IP
        with pytest.raises(AuthenticationError) as exc_info:
            await dep(request)

        assert exc_info.value.code == "CERT_REQUIRED"

    @pytest.mark.asyncio
    async def test_transport_with_no_ssl_object(self):
        """When transport exists but has no ssl_object, cert is None."""
        mock_crypto = MagicMock()
        mock_store = MagicMock()

        dep = create_auth_dependency(
            config=MagicMock(),
            store=mock_store,
            crypto=mock_crypto,
        )

        mock_transport = MagicMock()
        mock_transport.get_extra_info.return_value = None

        request = MagicMock()
        request.scope = {"transport": mock_transport}
        request.client = MagicMock()
        request.client.host = "10.0.0.5"

        with pytest.raises(AuthenticationError) as exc_info:
            await dep(request)

        assert exc_info.value.code == "CERT_REQUIRED"

    @pytest.mark.asyncio
    async def test_ssl_object_no_peer_cert(self):
        """When ssl_object exists but getpeercert returns None, cert is None."""
        mock_crypto = MagicMock()
        mock_store = MagicMock()

        dep = create_auth_dependency(
            config=MagicMock(),
            store=mock_store,
            crypto=mock_crypto,
        )

        mock_ssl_obj = MagicMock()
        mock_ssl_obj.getpeercert.return_value = None

        mock_transport = MagicMock()
        mock_transport.get_extra_info.return_value = mock_ssl_obj

        request = MagicMock()
        request.scope = {"transport": mock_transport}
        request.client = MagicMock()
        request.client.host = "10.0.0.6"

        with pytest.raises(AuthenticationError) as exc_info:
            await dep(request)

        assert exc_info.value.code == "CERT_REQUIRED"
