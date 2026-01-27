"""Cryptographic operations for ELLE Mobile Gateway.

This module handles:
- TLS certificate generation (self-signed CA and server cert)
- Client certificate signing for mTLS
- Certificate fingerprint computation
- Certificate verification

Certificates are stored in /var/lib/elle/mobile/
"""

import logging
from datetime import datetime, timedelta
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
from typing import NamedTuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from elle.mobile.config import MobileGatewayConfig, get_mobile_config

logger = logging.getLogger(__name__)


# Certificate validity periods
CA_VALIDITY_DAYS = 3650  # 10 years
SERVER_VALIDITY_DAYS = 365  # 1 year
CLIENT_VALIDITY_DAYS = 365  # 1 year

# Key size
RSA_KEY_SIZE = 4096


class CertificatePaths(NamedTuple):
    """Paths to certificate files."""

    ca_cert: Path
    ca_key: Path
    server_cert: Path
    server_key: Path


class ClientCertificate(NamedTuple):
    """Generated client certificate and key."""

    cert_pem: bytes
    key_pem: bytes
    fingerprint: str


class MobileCrypto:
    """Cryptographic operations for mobile gateway.

    Manages TLS certificates for the gateway server and client mTLS.
    """

    def __init__(self, config: MobileGatewayConfig | None = None):
        """Initialize crypto manager.

        Args:
            config: Mobile gateway configuration. Uses global config if None.
        """
        self.config = config or get_mobile_config()
        self.cert_dir = self.config.cert_dir

    def ensure_certificates(self) -> CertificatePaths:
        """Ensure all required certificates exist, generating if needed.

        Returns:
            Paths to all certificate files.
        """
        self.cert_dir.mkdir(parents=True, exist_ok=True)

        paths = CertificatePaths(
            ca_cert=self.cert_dir / "ca.crt",
            ca_key=self.cert_dir / "ca.key",
            server_cert=self.cert_dir / "server.crt",
            server_key=self.cert_dir / "server.key",
        )

        # Generate CA if missing
        if not paths.ca_cert.exists() or not paths.ca_key.exists():
            logger.info("Generating CA certificate")
            self._generate_ca(paths.ca_cert, paths.ca_key)

        # Generate server cert if missing
        if not paths.server_cert.exists() or not paths.server_key.exists():
            logger.info("Generating server certificate")
            self._generate_server_cert(
                paths.ca_cert,
                paths.ca_key,
                paths.server_cert,
                paths.server_key,
            )

        return paths

    def generate_client_certificate(self, device_id: str, device_name: str) -> ClientCertificate:
        """Generate a client certificate for mTLS.

        Args:
            device_id: Unique device identifier (embedded in cert CN).
            device_name: Human-readable name (embedded in cert OU).

        Returns:
            ClientCertificate with PEM-encoded cert, key, and fingerprint.
        """
        paths = self.ensure_certificates()

        # Load CA
        ca_cert = self._load_cert(paths.ca_cert)
        ca_key = self._load_key(paths.ca_key)

        # Generate client key
        client_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=RSA_KEY_SIZE,
        )

        # Build client certificate
        subject = x509.Name(
            [
                x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ELLE Mobile Client"),
                x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, device_name[:64]),
                x509.NameAttribute(NameOID.COMMON_NAME, f"device-{device_id}"),
            ]
        )

        now = datetime.utcnow()
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_cert.subject)
            .public_key(client_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=CLIENT_VALIDITY_DAYS))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=True,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())
        )

        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = client_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        fingerprint = self.compute_fingerprint(cert_pem)

        return ClientCertificate(
            cert_pem=cert_pem,
            key_pem=key_pem,
            fingerprint=fingerprint,
        )

    def get_server_fingerprint(self) -> str:
        """Get SHA-256 fingerprint of server certificate.

        Returns:
            Hex-encoded SHA-256 fingerprint.
        """
        paths = self.ensure_certificates()
        cert_pem = paths.server_cert.read_bytes()
        return self.compute_fingerprint(cert_pem)

    def compute_fingerprint(self, cert_pem: bytes) -> str:
        """Compute SHA-256 fingerprint of a PEM certificate.

        Args:
            cert_pem: PEM-encoded certificate.

        Returns:
            Hex-encoded SHA-256 fingerprint.
        """
        cert = x509.load_pem_x509_certificate(cert_pem)
        return str(cert.fingerprint(hashes.SHA256()).hex())

    def verify_client_cert(self, cert_pem: bytes) -> tuple[bool, str | None]:
        """Verify a client certificate against our CA.

        Args:
            cert_pem: PEM-encoded client certificate.

        Returns:
            Tuple of (is_valid, device_id or error_message).
        """
        try:
            paths = self.ensure_certificates()
            ca_cert = self._load_cert(paths.ca_cert)
            client_cert = x509.load_pem_x509_certificate(cert_pem)

            # Check issuer matches our CA
            if client_cert.issuer != ca_cert.subject:
                return False, "Certificate not issued by our CA"

            # Check validity period
            now = datetime.utcnow()
            if client_cert.not_valid_before > now:
                return False, "Certificate not yet valid"
            if client_cert.not_valid_after < now:
                return False, "Certificate expired"

            # Verify signature
            try:
                ca_cert.public_key().verify(
                    client_cert.signature,
                    client_cert.tbs_certificate_bytes,
                    client_cert.signature_algorithm_parameters,
                )
            except Exception:
                return False, "Certificate signature invalid"

            # Extract device_id from CN
            cn = client_cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            if not cn:
                return False, "Certificate missing CN"
            cn_value = cn[0].value
            if cn_value.startswith("device-"):
                device_id = cn_value[7:]  # Remove "device-" prefix
                return True, device_id
            return False, "Invalid CN format"

        except Exception as e:
            logger.exception("Certificate verification failed")
            return False, str(e)

    def _generate_ca(self, cert_path: Path, key_path: Path) -> None:
        """Generate a self-signed CA certificate."""
        # Generate CA key
        ca_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=RSA_KEY_SIZE,
        )

        # Build CA certificate
        subject = issuer = x509.Name(
            [
                x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ELLE Mobile Gateway"),
                x509.NameAttribute(NameOID.COMMON_NAME, "ELLE Mobile CA"),
            ]
        )

        now = datetime.utcnow()
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=CA_VALIDITY_DAYS))
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=0),
                critical=True,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=False,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())
        )

        # Write certificate
        cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

        # Write key with restricted permissions
        key_path.write_bytes(
            ca_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        key_path.chmod(0o600)

        logger.info("Generated CA certificate: %s", cert_path)

    def _generate_server_cert(
        self,
        ca_cert_path: Path,
        ca_key_path: Path,
        server_cert_path: Path,
        server_key_path: Path,
    ) -> None:
        """Generate server certificate signed by CA."""
        ca_cert = self._load_cert(ca_cert_path)
        ca_key = self._load_key(ca_key_path)

        # Generate server key
        server_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=RSA_KEY_SIZE,
        )

        # Build server certificate
        subject = x509.Name(
            [
                x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ELLE Mobile Gateway"),
                x509.NameAttribute(NameOID.COMMON_NAME, "ELLE Mobile Server"),
            ]
        )

        # Build SAN extension
        san_names = [
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress_from_string("127.0.0.1")),
            x509.IPAddress(ipaddress_from_string("::1")),
        ]

        # Add configured bind host if it's an IP
        bind_host = self.config.bind_host
        if bind_host and bind_host not in ("0.0.0.0", "::"):
            try:
                san_names.append(x509.IPAddress(ipaddress_from_string(bind_host)))
            except ValueError:
                san_names.append(x509.DNSName(bind_host))

        # Add overlay host if configured
        if self.config.overlay_host:
            try:
                san_names.append(x509.IPAddress(ipaddress_from_string(self.config.overlay_host)))
            except ValueError:
                san_names.append(x509.DNSName(self.config.overlay_host))

        now = datetime.utcnow()
        server_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_cert.subject)
            .public_key(server_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=SERVER_VALIDITY_DAYS))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=True,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=True,
            )
            .add_extension(
                x509.SubjectAlternativeName(san_names),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )

        # Write certificate
        server_cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))

        # Write key with restricted permissions
        server_key_path.write_bytes(
            server_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        server_key_path.chmod(0o600)

        logger.info("Generated server certificate: %s", server_cert_path)

    def _load_cert(self, path: Path) -> x509.Certificate:
        """Load a PEM certificate from disk."""
        return x509.load_pem_x509_certificate(path.read_bytes())

    def _load_key(self, path: Path) -> rsa.RSAPrivateKey:
        """Load a PEM private key from disk."""
        return serialization.load_pem_private_key(
            path.read_bytes(),
            password=None,
        )


def ipaddress_from_string(addr: str) -> IPv4Address | IPv6Address:
    """Convert IP address string to ipaddress object."""
    from ipaddress import ip_address

    return ip_address(addr)
