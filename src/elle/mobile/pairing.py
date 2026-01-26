"""QR-based device pairing for ELLE Mobile Gateway.

This module handles the pairing flow:
1. CLI initiates pairing, generates token
2. Token + server info encoded in QR code
3. Mobile scans QR, sends pairing request with token
4. Gateway validates token, generates client cert, returns to device
5. Device stores cert for future mTLS authentication
"""

import io
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import NamedTuple

try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_L

    QRCODE_AVAILABLE = True
except ImportError:
    QRCODE_AVAILABLE = False

from elle.mobile.config import MobileGatewayConfig, get_mobile_config
from elle.mobile.crypto import MobileCrypto
from elle.mobile.models import (
    DeviceStatus,
    MobileRole,
    PairedDevice,
    PairingToken,
    QRPayload,
)
from elle.mobile.store import MobileStore

logger = logging.getLogger(__name__)


# Token length in bytes (64 bytes = 128 hex chars)
TOKEN_BYTES = 64


class PairingResult(NamedTuple):
    """Result of a successful pairing."""

    device: PairedDevice
    client_cert_pem: bytes
    client_key_pem: bytes


class PairingError(Exception):
    """Error during pairing process."""

    pass


class PairingManager:
    """Manages device pairing flow.

    Handles token generation, QR code creation, and pairing completion.
    """

    def __init__(
        self,
        config: MobileGatewayConfig | None = None,
        store: MobileStore | None = None,
        crypto: MobileCrypto | None = None,
    ):
        """Initialize pairing manager.

        Args:
            config: Mobile gateway configuration.
            store: Mobile store instance.
            crypto: Mobile crypto instance.
        """
        self.config = config or get_mobile_config()
        self.store = store or MobileStore(self.config)
        self.crypto = crypto or MobileCrypto(self.config)

    def initiate_pairing(self, role: MobileRole | None = None) -> tuple[QRPayload, PairingToken]:
        """Initiate a new pairing flow.

        Generates a one-time token and returns QR payload.

        Args:
            role: Role to assign to paired device. Uses config default if None.

        Returns:
            Tuple of (QRPayload for display, PairingToken for validation).

        Raises:
            PairingError: If max devices reached or other error.
        """
        # Check device limit
        paired_count = self.store.count_devices(DeviceStatus.PAIRED)
        if paired_count >= self.config.max_paired_devices:
            raise PairingError(
                f"Maximum paired devices ({self.config.max_paired_devices}) reached. Revoke an existing device first."
            )

        # Determine role
        if role is None:
            role = MobileRole(self.config.default_role)

        # Generate secure token
        token_value = secrets.token_hex(TOKEN_BYTES)
        expires_at = datetime.utcnow() + timedelta(seconds=self.config.pairing_token_ttl_seconds)

        token = PairingToken(
            token=token_value,
            expires_at=expires_at,
            role=role,
        )

        # Store token
        self.store.create_token(token)

        # Get server fingerprint for pinning
        server_fingerprint = self.crypto.get_server_fingerprint()

        # Determine host to advertise
        host = self.config.overlay_host or self._get_advertise_host()

        payload = QRPayload(
            host=host,
            port=self.config.bind_port,
            token=token_value,
            server_fingerprint=server_fingerprint,
        )

        logger.info(
            "Initiated pairing with token expiring at %s",
            expires_at.isoformat(),
        )

        return payload, token

    def complete_pairing(self, token_value: str, device_name: str) -> PairingResult:
        """Complete pairing with a valid token.

        Validates the token, generates client certificate, and creates device.

        Args:
            token_value: The pairing token from QR code.
            device_name: Human-readable device name.

        Returns:
            PairingResult with device and client certificate.

        Raises:
            PairingError: If token invalid, expired, or already used.
        """
        # Validate token
        token = self.store.get_token(token_value)

        if token is None:
            raise PairingError("Invalid pairing token")

        if token.used:
            raise PairingError("Pairing token already used")

        if not token.is_valid():
            raise PairingError("Pairing token expired")

        # Mark token as used immediately to prevent race conditions
        self.store.mark_token_used(token_value)

        # Generate device ID
        device_id = str(uuid.uuid4())

        # Generate client certificate
        client_cert = self.crypto.generate_client_certificate(device_id, device_name)

        # Create device record
        now = datetime.utcnow()
        device = PairedDevice(
            device_id=device_id,
            name=device_name,
            role=token.role,
            status=DeviceStatus.PAIRED,
            cert_fingerprint=client_cert.fingerprint,
            paired_at=now,
            created_at=now,
        )

        self.store.create_device(device)

        logger.info(
            "Completed pairing for device %s (%s) with role %s",
            device_id,
            device_name,
            token.role.value,
        )

        return PairingResult(
            device=device,
            client_cert_pem=client_cert.cert_pem,
            client_key_pem=client_cert.key_pem,
        )

    def generate_qr_terminal(self, payload: QRPayload) -> str:
        """Generate ASCII QR code for terminal display.

        Args:
            payload: QR payload to encode.

        Returns:
            ASCII art QR code string.

        Raises:
            ImportError: If qrcode library not available.
        """
        if not QRCODE_AVAILABLE:
            raise ImportError("qrcode library required for QR generation. Install with: pip install 'elle[mobile]'")

        qr = qrcode.QRCode(
            version=None,  # Auto-size
            error_correction=ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(payload.to_json())
        qr.make(fit=True)

        # Generate ASCII representation
        output = io.StringIO()

        # Use Unicode block characters for compact display
        for r in range(0, qr.modules_count, 2):
            for c in range(qr.modules_count):
                top = qr.modules[r][c] if r < qr.modules_count else False
                bottom = qr.modules[r + 1][c] if r + 1 < qr.modules_count else False

                if top and bottom:
                    output.write("\u2588")  # Full block
                elif top:
                    output.write("\u2580")  # Upper half block
                elif bottom:
                    output.write("\u2584")  # Lower half block
                else:
                    output.write(" ")
            output.write("\n")

        return output.getvalue()

    def generate_qr_ascii_simple(self, payload: QRPayload) -> str:
        """Generate simple ASCII QR code (for terminals without Unicode).

        Args:
            payload: QR payload to encode.

        Returns:
            Simple ASCII QR code using # and spaces.
        """
        if not QRCODE_AVAILABLE:
            raise ImportError("qrcode library required for QR generation. Install with: pip install 'elle[mobile]'")

        qr = qrcode.QRCode(
            version=None,
            error_correction=ERROR_CORRECT_L,
            box_size=1,
            border=2,
        )
        qr.add_data(payload.to_json())
        qr.make(fit=True)

        output = io.StringIO()
        for row in qr.modules:
            for module in row:
                output.write("##" if module else "  ")
            output.write("\n")

        return output.getvalue()

    def _get_advertise_host(self) -> str:
        """Determine the host address to advertise.

        Returns the bind host if specific, otherwise attempts to
        detect a LAN IP address.
        """
        bind_host = self.config.bind_host

        # If binding to specific address, use that
        if bind_host not in ("0.0.0.0", "::", ""):
            return bind_host

        # Try to detect LAN IP
        try:
            import socket

            # Create dummy connection to detect local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # Doesn't actually connect, just sets up routing
                s.connect(("10.255.255.255", 1))
                ip = s.getsockname()[0]
            except Exception:
                ip = "127.0.0.1"
            finally:
                s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def get_pairing_instructions(self, payload: QRPayload) -> str:
        """Get human-readable pairing instructions.

        Args:
            payload: QR payload for pairing.

        Returns:
            Formatted instructions string.
        """
        lines = [
            "Mobile Gateway Pairing",
            "=" * 50,
            "",
            "Scan the QR code with the ELLE mobile app to pair.",
            "",
            f"Server: {payload.host}:{payload.port}",
            f"Token expires in {self.config.pairing_token_ttl_seconds} seconds",
            "",
            "Or manually enter:",
            f"  Host: {payload.host}",
            f"  Port: {payload.port}",
            f"  Token: {payload.token[:16]}...",
            "",
        ]
        return "\n".join(lines)
