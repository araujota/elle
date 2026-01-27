"""Authentication middleware for ELLE Mobile Gateway.

This module provides mTLS-based authentication for mobile devices.
Every request must present a valid client certificate signed by our CA.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import Request

from pydantic import BaseModel, Field

from elle.mobile.config import MobileGatewayConfig, get_mobile_config
from elle.mobile.crypto import MobileCrypto
from elle.mobile.models import DeviceStatus, MobileRole
from elle.mobile.store import MobileStore

logger = logging.getLogger(__name__)


class MobileAuthContext(BaseModel):
    """Authentication context for a mobile request.

    Contains information about the authenticated device and its
    current effective permissions.

    Attributes:
        device_id: Unique device identifier
        device_name: Human-readable device name
        role: Base role assigned to device
        effective_role: Current role (may be elevated)
        elevation_expires_at: When elevation expires (if elevated)
        client_ip: Client IP address
        cert_fingerprint: Client certificate fingerprint
    """

    device_id: str = Field(..., description="Device identifier")
    device_name: str = Field(..., description="Device name")
    role: MobileRole = Field(..., description="Base role")
    effective_role: MobileRole = Field(..., description="Effective role")
    elevation_expires_at: datetime | None = Field(default=None, description="Elevation expiration")
    client_ip: str = Field(..., description="Client IP address")
    cert_fingerprint: str = Field(..., description="Certificate fingerprint")

    model_config = {"frozen": True}

    @property
    def is_elevated(self) -> bool:
        """Check if device is currently elevated."""
        return self.effective_role != self.role


class AuthenticationError(Exception):
    """Authentication failed."""

    def __init__(self, message: str, code: str = "AUTH_FAILED"):
        super().__init__(message)
        self.code = code


class MobileAuthenticator:
    """Authenticates mobile devices via mTLS.

    Validates client certificates and builds authentication context.
    """

    def __init__(
        self,
        config: MobileGatewayConfig | None = None,
        store: MobileStore | None = None,
        crypto: MobileCrypto | None = None,
    ):
        """Initialize authenticator.

        Args:
            config: Mobile gateway configuration.
            store: Mobile store instance.
            crypto: Mobile crypto instance.
        """
        self.config = config or get_mobile_config()
        self.store = store or MobileStore(self.config)
        self.crypto = crypto or MobileCrypto(self.config)

    def authenticate(self, client_cert_pem: bytes | None, client_ip: str) -> MobileAuthContext:
        """Authenticate a request using client certificate.

        Args:
            client_cert_pem: PEM-encoded client certificate from TLS handshake.
            client_ip: Client IP address.

        Returns:
            MobileAuthContext for the authenticated device.

        Raises:
            AuthenticationError: If authentication fails.
        """
        # mTLS is required
        if client_cert_pem is None:
            raise AuthenticationError(
                "Client certificate required",
                code="CERT_REQUIRED",
            )

        # Verify certificate against CA
        valid, result = self.crypto.verify_client_cert(client_cert_pem)
        if not valid:
            logger.warning(
                "Certificate verification failed from %s: %s",
                client_ip,
                result,
            )
            raise AuthenticationError(
                f"Certificate verification failed: {result}",
                code="CERT_INVALID",
            )

        device_id = result
        if device_id is None:
            raise AuthenticationError(
                "Invalid certificate: no device ID",
                code="CERT_INVALID",
            )

        # Look up device
        device = self.store.get_device(device_id)
        if device is None:
            logger.warning(
                "Unknown device %s attempted connection from %s",
                device_id,
                client_ip,
            )
            raise AuthenticationError(
                "Device not registered",
                code="DEVICE_UNKNOWN",
            )

        # Check device status
        if device.status == DeviceStatus.REVOKED:
            logger.warning(
                "Revoked device %s attempted connection from %s",
                device_id,
                client_ip,
            )
            raise AuthenticationError(
                "Device access revoked",
                code="DEVICE_REVOKED",
            )

        if device.status != DeviceStatus.PAIRED:
            raise AuthenticationError(
                f"Device status invalid: {device.status}",
                code="DEVICE_STATUS_INVALID",
            )

        # Check for active elevation
        elevation = self.store.get_active_elevation(device_id)
        effective_role = device.role
        elevation_expires_at = None

        if elevation and elevation.is_active():
            effective_role = elevation.elevated_role
            elevation_expires_at = elevation.expires_at
            logger.debug(
                "Device %s has active elevation to %s until %s",
                device_id,
                effective_role.value,
                elevation_expires_at.isoformat(),
            )

        # Update last seen
        self.store.update_device(device_id, last_seen_at=datetime.utcnow())

        # Compute fingerprint for context
        fingerprint = self.crypto.compute_fingerprint(client_cert_pem)

        return MobileAuthContext(
            device_id=device.device_id,
            device_name=device.name,
            role=device.role,
            effective_role=effective_role,
            elevation_expires_at=elevation_expires_at,
            client_ip=client_ip,
            cert_fingerprint=fingerprint,
        )


def create_auth_dependency(
    config: MobileGatewayConfig | None = None,
    store: MobileStore | None = None,
    crypto: MobileCrypto | None = None,
) -> Callable[..., Any]:
    """Create a FastAPI dependency for authentication.

    Args:
        config: Mobile gateway configuration.
        store: Mobile store instance.
        crypto: Mobile crypto instance.

    Returns:
        FastAPI dependency function.
    """
    authenticator = MobileAuthenticator(config, store, crypto)

    async def get_auth_context(request: "Request") -> MobileAuthContext:
        """FastAPI dependency that extracts auth context from request.

        The client certificate is provided by the TLS layer and stored
        in the request's transport context.
        """
        # Get client certificate from TLS
        client_cert_pem = None

        # Try to get from transport (uvicorn with SSL)
        if hasattr(request, "scope"):
            transport = request.scope.get("transport")
            if transport:
                ssl_object = transport.get_extra_info("ssl_object")
                if ssl_object:
                    peer_cert = ssl_object.getpeercert(binary_form=True)
                    if peer_cert:
                        # Convert DER to PEM
                        from cryptography import x509
                        from cryptography.hazmat.primitives import serialization

                        cert = x509.load_der_x509_certificate(peer_cert)
                        client_cert_pem = cert.public_bytes(serialization.Encoding.PEM)

        # Get client IP
        client_ip = "unknown"
        if hasattr(request, "client") and request.client:
            client_ip = request.client.host

        return authenticator.authenticate(client_cert_pem, client_ip)

    return get_auth_context
