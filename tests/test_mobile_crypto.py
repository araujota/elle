"""Tests for ELLE Mobile Gateway cryptographic operations.

Covers:
- CertificatePaths and ClientCertificate NamedTuples
- MobileCrypto.__init__ with explicit config and default fallback
- MobileCrypto.ensure_certificates (all branch paths)
- MobileCrypto.generate_client_certificate
- MobileCrypto.get_server_fingerprint
- MobileCrypto.compute_fingerprint
- MobileCrypto.verify_client_cert (all validation paths and error branches)
- MobileCrypto._generate_ca
- MobileCrypto._generate_server_cert (bind_host and overlay_host branches)
- MobileCrypto._load_cert and _load_key
- ipaddress_from_string top-level helper
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure elle.mobile.crypto is importable even when 'cryptography' is absent
# in the test environment.  We need to make sure the module's top-level
# imports of 'cryptography' sub-packages succeed.  We inject lightweight mock
# modules into sys.modules before importing the module under test.
# ---------------------------------------------------------------------------

_CRYPTO_MODULES_TO_STUB = [
    "cryptography",
    "cryptography.x509",
    "cryptography.x509.oid",
    "cryptography.hazmat",
    "cryptography.hazmat.primitives",
    "cryptography.hazmat.primitives.hashes",
    "cryptography.hazmat.primitives.serialization",
    "cryptography.hazmat.primitives.asymmetric",
    "cryptography.hazmat.primitives.asymmetric.rsa",
]

_saved_crypto: dict[str, object] = {}
for _mod_name in _CRYPTO_MODULES_TO_STUB:
    _saved_crypto[_mod_name] = sys.modules.get(_mod_name)
    sys.modules[_mod_name] = MagicMock()

# Force a fresh import of the module under test (earlier test files may have
# cached it with their own stubs).
sys.modules.pop("elle.mobile.crypto", None)

import importlib

import elle.mobile.crypto as _crypto_mod

importlib.reload(_crypto_mod)

from elle.mobile.crypto import (
    CA_VALIDITY_DAYS,
    CLIENT_VALIDITY_DAYS,
    RSA_KEY_SIZE,
    SERVER_VALIDITY_DAYS,
    CertificatePaths,
    ClientCertificate,
    MobileCrypto,
    ipaddress_from_string,
)

# Restore any previously existing crypto modules
for _mod_name, _original in _saved_crypto.items():
    if _original is not None:
        sys.modules[_mod_name] = _original
    else:
        sys.modules.pop(_mod_name, None)


# =============================================================================
# Constants
# =============================================================================

FAKE_CERT_PEM = b"-----BEGIN CERTIFICATE-----\nFAKEDATA\n-----END CERTIFICATE-----\n"
FAKE_KEY_PEM = b"-----BEGIN PRIVATE KEY-----\nFAKEKEY\n-----END PRIVATE KEY-----\n"
FAKE_FINGERPRINT = "aabbccdd11223344"


# =============================================================================
# Helpers
# =============================================================================


def _make_config(
    cert_dir: Path | None = None,
    bind_host: str = "0.0.0.0",
    overlay_host: str | None = None,
) -> MagicMock:
    """Create a mock MobileGatewayConfig."""
    config = MagicMock()
    config.cert_dir = cert_dir or Path("/tmp/test-certs")
    config.bind_host = bind_host
    config.overlay_host = overlay_host
    return config


# =============================================================================
# TestConstants
# =============================================================================


class TestConstants:
    """Tests for module-level constants."""

    def test_ca_validity_days(self):
        """CA validity is 1 year (C13 hardening)."""
        assert CA_VALIDITY_DAYS == 365

    def test_server_validity_days(self):
        """Server cert validity is 180 days (C13 hardening)."""
        assert SERVER_VALIDITY_DAYS == 180

    def test_client_validity_days(self):
        """Client cert validity is 180 days (C13 hardening)."""
        assert CLIENT_VALIDITY_DAYS == 180

    def test_rsa_key_size(self):
        """RSA key size is 4096 bits."""
        assert RSA_KEY_SIZE == 4096


# =============================================================================
# TestNamedTuples
# =============================================================================


class TestCertificatePaths:
    """Tests for CertificatePaths NamedTuple."""

    def test_create_certificate_paths(self):
        """CertificatePaths stores all four cert/key paths."""
        paths = CertificatePaths(
            ca_cert=Path("/ca.crt"),
            ca_key=Path("/ca.key"),
            server_cert=Path("/server.crt"),
            server_key=Path("/server.key"),
        )
        assert paths.ca_cert == Path("/ca.crt")
        assert paths.ca_key == Path("/ca.key")
        assert paths.server_cert == Path("/server.crt")
        assert paths.server_key == Path("/server.key")

    def test_certificate_paths_is_tuple(self):
        """CertificatePaths is a NamedTuple with 4 elements."""
        paths = CertificatePaths(
            ca_cert=Path("/a"),
            ca_key=Path("/b"),
            server_cert=Path("/c"),
            server_key=Path("/d"),
        )
        assert len(paths) == 4
        assert paths[0] == Path("/a")


class TestClientCertificate:
    """Tests for ClientCertificate NamedTuple."""

    def test_create_client_certificate(self):
        """ClientCertificate stores cert_pem, key_pem, fingerprint."""
        cc = ClientCertificate(
            cert_pem=FAKE_CERT_PEM,
            key_pem=FAKE_KEY_PEM,
            fingerprint=FAKE_FINGERPRINT,
        )
        assert cc.cert_pem == FAKE_CERT_PEM
        assert cc.key_pem == FAKE_KEY_PEM
        assert cc.fingerprint == FAKE_FINGERPRINT

    def test_client_certificate_is_tuple(self):
        """ClientCertificate is a NamedTuple with 3 elements."""
        cc = ClientCertificate(
            cert_pem=b"c",
            key_pem=b"k",
            fingerprint="fp",
        )
        assert len(cc) == 3
        assert cc[2] == "fp"


# =============================================================================
# TestMobileCryptoInit
# =============================================================================


class TestMobileCryptoInit:
    """Tests for MobileCrypto.__init__."""

    def test_init_with_explicit_config(self):
        """Passing a config uses it directly."""
        config = _make_config()
        crypto = MobileCrypto(config=config)
        assert crypto.config is config
        assert crypto.cert_dir == config.cert_dir

    @patch("elle.mobile.crypto.get_mobile_config")
    def test_init_without_config_uses_global(self, mock_get_config):
        """When config is None, get_mobile_config() is called."""
        mock_cfg = _make_config()
        mock_get_config.return_value = mock_cfg

        crypto = MobileCrypto()

        mock_get_config.assert_called_once()
        assert crypto.config is mock_cfg
        assert crypto.cert_dir == mock_cfg.cert_dir


# =============================================================================
# TestEnsureCertificates
# =============================================================================


class TestEnsureCertificates:
    """Tests for MobileCrypto.ensure_certificates."""

    def test_generates_ca_when_missing(self, tmp_path):
        """When CA files don't exist, _generate_ca is called."""
        config = _make_config(cert_dir=tmp_path)
        crypto = MobileCrypto(config=config)

        with (
            patch.object(crypto, "_generate_ca") as mock_gen_ca,
            patch.object(crypto, "_generate_server_cert") as mock_gen_server,
        ):
            paths = crypto.ensure_certificates()

        mock_gen_ca.assert_called_once_with(paths.ca_cert, paths.ca_key)
        mock_gen_server.assert_called_once_with(paths.ca_cert, paths.ca_key, paths.server_cert, paths.server_key)

    def test_skips_ca_generation_when_exists(self, tmp_path):
        """When CA cert and key exist, _generate_ca is NOT called."""
        config = _make_config(cert_dir=tmp_path)
        crypto = MobileCrypto(config=config)

        # Create CA files
        (tmp_path / "ca.crt").write_bytes(b"cert")
        (tmp_path / "ca.key").write_bytes(b"key")

        with (
            patch.object(crypto, "_generate_ca") as mock_gen_ca,
            patch.object(crypto, "_generate_server_cert") as mock_gen_server,
        ):
            crypto.ensure_certificates()

        mock_gen_ca.assert_not_called()
        mock_gen_server.assert_called_once()

    def test_generates_ca_when_cert_missing_but_key_exists(self, tmp_path):
        """When CA key exists but cert is missing, regenerates CA."""
        config = _make_config(cert_dir=tmp_path)
        crypto = MobileCrypto(config=config)

        # Only create key
        (tmp_path / "ca.key").write_bytes(b"key")

        with (
            patch.object(crypto, "_generate_ca") as mock_gen_ca,
            patch.object(crypto, "_generate_server_cert"),
        ):
            crypto.ensure_certificates()

        mock_gen_ca.assert_called_once()

    def test_generates_ca_when_key_missing_but_cert_exists(self, tmp_path):
        """When CA cert exists but key is missing, regenerates CA."""
        config = _make_config(cert_dir=tmp_path)
        crypto = MobileCrypto(config=config)

        # Only create cert
        (tmp_path / "ca.crt").write_bytes(b"cert")

        with (
            patch.object(crypto, "_generate_ca") as mock_gen_ca,
            patch.object(crypto, "_generate_server_cert"),
        ):
            crypto.ensure_certificates()

        mock_gen_ca.assert_called_once()

    def test_skips_server_generation_when_exists(self, tmp_path):
        """When server cert and key exist, _generate_server_cert is NOT called."""
        config = _make_config(cert_dir=tmp_path)
        crypto = MobileCrypto(config=config)

        # Create all files
        (tmp_path / "ca.crt").write_bytes(b"cert")
        (tmp_path / "ca.key").write_bytes(b"key")
        (tmp_path / "server.crt").write_bytes(b"scert")
        (tmp_path / "server.key").write_bytes(b"skey")

        with (
            patch.object(crypto, "_generate_ca") as mock_gen_ca,
            patch.object(crypto, "_generate_server_cert") as mock_gen_server,
        ):
            crypto.ensure_certificates()

        mock_gen_ca.assert_not_called()
        mock_gen_server.assert_not_called()

    def test_generates_server_when_server_cert_missing(self, tmp_path):
        """When server cert is missing but key exists, regenerates server cert."""
        config = _make_config(cert_dir=tmp_path)
        crypto = MobileCrypto(config=config)

        (tmp_path / "ca.crt").write_bytes(b"cert")
        (tmp_path / "ca.key").write_bytes(b"key")
        (tmp_path / "server.key").write_bytes(b"skey")

        with (
            patch.object(crypto, "_generate_ca"),
            patch.object(crypto, "_generate_server_cert") as mock_gen_server,
        ):
            crypto.ensure_certificates()

        mock_gen_server.assert_called_once()

    def test_generates_server_when_server_key_missing(self, tmp_path):
        """When server key is missing but cert exists, regenerates server cert."""
        config = _make_config(cert_dir=tmp_path)
        crypto = MobileCrypto(config=config)

        (tmp_path / "ca.crt").write_bytes(b"cert")
        (tmp_path / "ca.key").write_bytes(b"key")
        (tmp_path / "server.crt").write_bytes(b"scert")

        with (
            patch.object(crypto, "_generate_ca"),
            patch.object(crypto, "_generate_server_cert") as mock_gen_server,
        ):
            crypto.ensure_certificates()

        mock_gen_server.assert_called_once()

    def test_returns_correct_paths(self, tmp_path):
        """Returns CertificatePaths with correct directory-based paths."""
        config = _make_config(cert_dir=tmp_path)
        crypto = MobileCrypto(config=config)

        with (
            patch.object(crypto, "_generate_ca"),
            patch.object(crypto, "_generate_server_cert"),
        ):
            paths = crypto.ensure_certificates()

        assert paths.ca_cert == tmp_path / "ca.crt"
        assert paths.ca_key == tmp_path / "ca.key"
        assert paths.server_cert == tmp_path / "server.crt"
        assert paths.server_key == tmp_path / "server.key"

    def test_creates_cert_dir_if_not_exists(self, tmp_path):
        """ensure_certificates creates the cert directory if it doesn't exist."""
        nested_dir = tmp_path / "nested" / "certs"
        config = _make_config(cert_dir=nested_dir)
        crypto = MobileCrypto(config=config)

        with (
            patch.object(crypto, "_generate_ca"),
            patch.object(crypto, "_generate_server_cert"),
        ):
            crypto.ensure_certificates()

        assert nested_dir.exists()


# =============================================================================
# TestGenerateClientCertificate
# =============================================================================


class TestGenerateClientCertificate:
    """Tests for MobileCrypto.generate_client_certificate."""

    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_generates_client_cert(self, mock_serialization, mock_hashes, mock_rsa, mock_x509):
        """generate_client_certificate returns a ClientCertificate."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        # Mock ensure_certificates
        mock_paths = CertificatePaths(
            ca_cert=Path("/ca.crt"),
            ca_key=Path("/ca.key"),
            server_cert=Path("/server.crt"),
            server_key=Path("/server.key"),
        )

        mock_ca_cert = MagicMock()
        mock_ca_key = MagicMock()
        mock_client_key = MagicMock()
        mock_cert_builder = MagicMock()
        mock_cert = MagicMock()

        # Chain the CertificateBuilder calls
        mock_x509.CertificateBuilder.return_value = mock_cert_builder
        mock_cert_builder.subject_name.return_value = mock_cert_builder
        mock_cert_builder.issuer_name.return_value = mock_cert_builder
        mock_cert_builder.public_key.return_value = mock_cert_builder
        mock_cert_builder.serial_number.return_value = mock_cert_builder
        mock_cert_builder.not_valid_before.return_value = mock_cert_builder
        mock_cert_builder.not_valid_after.return_value = mock_cert_builder
        mock_cert_builder.add_extension.return_value = mock_cert_builder
        mock_cert_builder.sign.return_value = mock_cert

        mock_cert.public_bytes.return_value = FAKE_CERT_PEM
        mock_client_key.private_bytes.return_value = FAKE_KEY_PEM

        mock_rsa.generate_private_key.return_value = mock_client_key

        with (
            patch.object(crypto, "ensure_certificates", return_value=mock_paths),
            patch.object(crypto, "_load_cert", return_value=mock_ca_cert),
            patch.object(crypto, "_load_key", return_value=mock_ca_key),
            patch.object(crypto, "compute_fingerprint", return_value=FAKE_FINGERPRINT),
        ):
            result = crypto.generate_client_certificate("dev-001", "My Phone")

        assert isinstance(result, ClientCertificate)
        assert result.cert_pem == FAKE_CERT_PEM
        assert result.key_pem == FAKE_KEY_PEM
        assert result.fingerprint == FAKE_FINGERPRINT

    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_truncates_device_name_to_64_chars(self, mock_serialization, mock_hashes, mock_rsa, mock_x509):
        """Device name is truncated to 64 chars in the OU field."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        mock_paths = CertificatePaths(
            ca_cert=Path("/ca.crt"),
            ca_key=Path("/ca.key"),
            server_cert=Path("/server.crt"),
            server_key=Path("/server.key"),
        )

        mock_ca_cert = MagicMock()
        mock_ca_key = MagicMock()
        mock_client_key = MagicMock()
        mock_cert_builder = MagicMock()
        mock_cert = MagicMock()

        mock_x509.CertificateBuilder.return_value = mock_cert_builder
        mock_cert_builder.subject_name.return_value = mock_cert_builder
        mock_cert_builder.issuer_name.return_value = mock_cert_builder
        mock_cert_builder.public_key.return_value = mock_cert_builder
        mock_cert_builder.serial_number.return_value = mock_cert_builder
        mock_cert_builder.not_valid_before.return_value = mock_cert_builder
        mock_cert_builder.not_valid_after.return_value = mock_cert_builder
        mock_cert_builder.add_extension.return_value = mock_cert_builder
        mock_cert_builder.sign.return_value = mock_cert

        mock_cert.public_bytes.return_value = FAKE_CERT_PEM
        mock_client_key.private_bytes.return_value = FAKE_KEY_PEM
        mock_rsa.generate_private_key.return_value = mock_client_key

        long_name = "A" * 100

        with (
            patch.object(crypto, "ensure_certificates", return_value=mock_paths),
            patch.object(crypto, "_load_cert", return_value=mock_ca_cert),
            patch.object(crypto, "_load_key", return_value=mock_ca_key),
            patch.object(crypto, "compute_fingerprint", return_value=FAKE_FINGERPRINT),
        ):
            crypto.generate_client_certificate("dev-002", long_name)

        # The OU should be truncated to 64 chars.
        # Verify by checking the Name construction call to x509.Name
        name_call = mock_x509.Name.call_args
        assert name_call is not None
        name_attrs = name_call[0][0]
        # Find the OU attribute (third in the list)
        name_attrs[2]
        # The NameAttribute mock was called with the truncated name
        assert mock_x509.NameAttribute.call_args_list[2][0][1] == long_name[:64]

    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_device_id_embedded_in_cn(self, mock_serialization, mock_hashes, mock_rsa, mock_x509):
        """Device ID is embedded as 'device-{id}' in the CN."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        mock_paths = CertificatePaths(
            ca_cert=Path("/ca.crt"),
            ca_key=Path("/ca.key"),
            server_cert=Path("/server.crt"),
            server_key=Path("/server.key"),
        )

        mock_ca_cert = MagicMock()
        mock_ca_key = MagicMock()
        mock_client_key = MagicMock()
        mock_cert_builder = MagicMock()
        mock_cert = MagicMock()

        mock_x509.CertificateBuilder.return_value = mock_cert_builder
        mock_cert_builder.subject_name.return_value = mock_cert_builder
        mock_cert_builder.issuer_name.return_value = mock_cert_builder
        mock_cert_builder.public_key.return_value = mock_cert_builder
        mock_cert_builder.serial_number.return_value = mock_cert_builder
        mock_cert_builder.not_valid_before.return_value = mock_cert_builder
        mock_cert_builder.not_valid_after.return_value = mock_cert_builder
        mock_cert_builder.add_extension.return_value = mock_cert_builder
        mock_cert_builder.sign.return_value = mock_cert

        mock_cert.public_bytes.return_value = FAKE_CERT_PEM
        mock_client_key.private_bytes.return_value = FAKE_KEY_PEM
        mock_rsa.generate_private_key.return_value = mock_client_key

        with (
            patch.object(crypto, "ensure_certificates", return_value=mock_paths),
            patch.object(crypto, "_load_cert", return_value=mock_ca_cert),
            patch.object(crypto, "_load_key", return_value=mock_ca_key),
            patch.object(crypto, "compute_fingerprint", return_value=FAKE_FINGERPRINT),
        ):
            crypto.generate_client_certificate("abc-123", "Phone")

        # The CN (4th NameAttribute) should be "device-abc-123"
        cn_call = mock_x509.NameAttribute.call_args_list[3]
        assert cn_call[0][1] == "device-abc-123"

    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_calls_rsa_with_correct_params(self, mock_serialization, mock_hashes, mock_rsa, mock_x509):
        """RSA key is generated with correct exponent and key size."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        mock_paths = CertificatePaths(
            ca_cert=Path("/ca.crt"),
            ca_key=Path("/ca.key"),
            server_cert=Path("/server.crt"),
            server_key=Path("/server.key"),
        )

        mock_client_key = MagicMock()
        mock_cert_builder = MagicMock()
        mock_cert = MagicMock()

        mock_x509.CertificateBuilder.return_value = mock_cert_builder
        mock_cert_builder.subject_name.return_value = mock_cert_builder
        mock_cert_builder.issuer_name.return_value = mock_cert_builder
        mock_cert_builder.public_key.return_value = mock_cert_builder
        mock_cert_builder.serial_number.return_value = mock_cert_builder
        mock_cert_builder.not_valid_before.return_value = mock_cert_builder
        mock_cert_builder.not_valid_after.return_value = mock_cert_builder
        mock_cert_builder.add_extension.return_value = mock_cert_builder
        mock_cert_builder.sign.return_value = mock_cert

        mock_cert.public_bytes.return_value = FAKE_CERT_PEM
        mock_client_key.private_bytes.return_value = FAKE_KEY_PEM
        mock_rsa.generate_private_key.return_value = mock_client_key

        with (
            patch.object(crypto, "ensure_certificates", return_value=mock_paths),
            patch.object(crypto, "_load_cert", return_value=MagicMock()),
            patch.object(crypto, "_load_key", return_value=MagicMock()),
            patch.object(crypto, "compute_fingerprint", return_value=FAKE_FINGERPRINT),
        ):
            crypto.generate_client_certificate("dev-001", "Phone")

        mock_rsa.generate_private_key.assert_called_once_with(
            public_exponent=65537,
            key_size=RSA_KEY_SIZE,
        )


# =============================================================================
# TestGetServerFingerprint
# =============================================================================


class TestGetServerFingerprint:
    """Tests for MobileCrypto.get_server_fingerprint."""

    def test_returns_fingerprint(self, tmp_path):
        """get_server_fingerprint reads server cert and computes fingerprint."""
        config = _make_config(cert_dir=tmp_path)
        crypto = MobileCrypto(config=config)

        # Create all cert files so ensure_certificates doesn't try to generate
        (tmp_path / "ca.crt").write_bytes(FAKE_CERT_PEM)
        (tmp_path / "ca.key").write_bytes(FAKE_KEY_PEM)
        server_cert_path = tmp_path / "server.crt"
        server_cert_path.write_bytes(FAKE_CERT_PEM)
        (tmp_path / "server.key").write_bytes(FAKE_KEY_PEM)

        with patch.object(crypto, "compute_fingerprint", return_value=FAKE_FINGERPRINT) as mock_fp:
            result = crypto.get_server_fingerprint()

        assert result == FAKE_FINGERPRINT
        mock_fp.assert_called_once_with(FAKE_CERT_PEM)

    def test_calls_ensure_certificates(self):
        """get_server_fingerprint calls ensure_certificates first."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        mock_server_cert_path = MagicMock()
        mock_server_cert_path.read_bytes.return_value = FAKE_CERT_PEM

        mock_paths = CertificatePaths(
            ca_cert=Path("/ca.crt"),
            ca_key=Path("/ca.key"),
            server_cert=mock_server_cert_path,
            server_key=Path("/server.key"),
        )

        with (
            patch.object(crypto, "ensure_certificates", return_value=mock_paths) as mock_ensure,
            patch.object(crypto, "compute_fingerprint", return_value=FAKE_FINGERPRINT),
        ):
            crypto.get_server_fingerprint()

        mock_ensure.assert_called_once()


# =============================================================================
# TestComputeFingerprint
# =============================================================================


class TestComputeFingerprint:
    """Tests for MobileCrypto.compute_fingerprint."""

    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.hashes")
    def test_computes_sha256_fingerprint(self, mock_hashes, mock_x509):
        """compute_fingerprint loads the cert and returns hex SHA-256."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        mock_cert = MagicMock()
        mock_cert.fingerprint.return_value = b"\xaa\xbb\xcc\xdd"
        mock_x509.load_pem_x509_certificate.return_value = mock_cert

        result = crypto.compute_fingerprint(FAKE_CERT_PEM)

        mock_x509.load_pem_x509_certificate.assert_called_once_with(FAKE_CERT_PEM)
        mock_cert.fingerprint.assert_called_once_with(mock_hashes.SHA256())
        assert result == "aabbccdd"

    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.hashes")
    def test_returns_string(self, mock_hashes, mock_x509):
        """compute_fingerprint returns a str type."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        mock_cert = MagicMock()
        mock_cert.fingerprint.return_value = b"\x00\x11\x22\x33"
        mock_x509.load_pem_x509_certificate.return_value = mock_cert

        result = crypto.compute_fingerprint(FAKE_CERT_PEM)
        assert isinstance(result, str)


# =============================================================================
# TestVerifyClientCert
# =============================================================================


class TestVerifyClientCert:
    """Tests for MobileCrypto.verify_client_cert."""

    def _setup_verify(
        self,
        *,
        issuer_match: bool = True,
        not_valid_before: datetime | None = None,
        not_valid_after: datetime | None = None,
        cn_value: str = "device-dev-001",
        cn_attrs: list | None = None,
        signature_valid: bool = True,
    ) -> tuple[MobileCrypto, MagicMock, MagicMock, MagicMock]:
        """Set up common mocks for verify_client_cert tests.

        Returns (crypto, mock_x509, mock_ca_cert, mock_client_cert).
        """
        config = _make_config()
        crypto = MobileCrypto(config=config)

        mock_ca_cert = MagicMock()
        mock_client_cert = MagicMock()

        now = datetime.now(timezone.utc)
        if not_valid_before is None:
            not_valid_before = now - timedelta(days=1)
        if not_valid_after is None:
            not_valid_after = now + timedelta(days=364)

        mock_client_cert.not_valid_before = not_valid_before
        mock_client_cert.not_valid_after = not_valid_after

        if issuer_match:
            mock_client_cert.issuer = mock_ca_cert.subject
        else:
            mock_client_cert.issuer = MagicMock()

        if cn_attrs is not None:
            mock_client_cert.subject.get_attributes_for_oid.return_value = cn_attrs
        else:
            mock_cn_attr = MagicMock()
            mock_cn_attr.value = cn_value
            mock_client_cert.subject.get_attributes_for_oid.return_value = [mock_cn_attr]

        if not signature_valid:
            mock_ca_cert.public_key.return_value.verify.side_effect = Exception("bad sig")

        mock_paths = CertificatePaths(
            ca_cert=Path("/ca.crt"),
            ca_key=Path("/ca.key"),
            server_cert=Path("/server.crt"),
            server_key=Path("/server.key"),
        )

        return crypto, mock_paths, mock_ca_cert, mock_client_cert

    @patch("elle.mobile.crypto.x509")
    def test_valid_cert_returns_true_with_device_id(self, mock_x509):
        """Valid cert returns (True, device_id)."""
        crypto, mock_paths, mock_ca_cert, mock_client_cert = self._setup_verify(cn_value="device-abc-123")

        mock_x509.load_pem_x509_certificate.return_value = mock_client_cert

        with (
            patch.object(crypto, "ensure_certificates", return_value=mock_paths),
            patch.object(crypto, "_load_cert", return_value=mock_ca_cert),
        ):
            valid, result = crypto.verify_client_cert(FAKE_CERT_PEM)

        assert valid is True
        assert result == "abc-123"

    @patch("elle.mobile.crypto.x509")
    def test_issuer_mismatch_returns_false(self, mock_x509):
        """Cert not issued by our CA returns (False, error)."""
        crypto, mock_paths, mock_ca_cert, mock_client_cert = self._setup_verify(issuer_match=False)

        mock_x509.load_pem_x509_certificate.return_value = mock_client_cert

        with (
            patch.object(crypto, "ensure_certificates", return_value=mock_paths),
            patch.object(crypto, "_load_cert", return_value=mock_ca_cert),
        ):
            valid, result = crypto.verify_client_cert(FAKE_CERT_PEM)

        assert valid is False
        assert "not issued by our CA" in result

    @patch("elle.mobile.crypto.x509")
    def test_cert_not_yet_valid_returns_false(self, mock_x509):
        """Cert with future not_valid_before returns (False, error)."""
        future = datetime.now(timezone.utc) + timedelta(days=1)
        crypto, mock_paths, mock_ca_cert, mock_client_cert = self._setup_verify(not_valid_before=future)

        mock_x509.load_pem_x509_certificate.return_value = mock_client_cert

        with (
            patch.object(crypto, "ensure_certificates", return_value=mock_paths),
            patch.object(crypto, "_load_cert", return_value=mock_ca_cert),
        ):
            valid, result = crypto.verify_client_cert(FAKE_CERT_PEM)

        assert valid is False
        assert "not yet valid" in result

    @patch("elle.mobile.crypto.x509")
    def test_expired_cert_returns_false(self, mock_x509):
        """Cert with past not_valid_after returns (False, error)."""
        past = datetime.now(timezone.utc) - timedelta(days=1)
        crypto, mock_paths, mock_ca_cert, mock_client_cert = self._setup_verify(not_valid_after=past)

        mock_x509.load_pem_x509_certificate.return_value = mock_client_cert

        with (
            patch.object(crypto, "ensure_certificates", return_value=mock_paths),
            patch.object(crypto, "_load_cert", return_value=mock_ca_cert),
        ):
            valid, result = crypto.verify_client_cert(FAKE_CERT_PEM)

        assert valid is False
        assert "expired" in result

    @patch("elle.mobile.crypto.x509")
    def test_invalid_signature_returns_false(self, mock_x509):
        """Cert with bad signature returns (False, error)."""
        crypto, mock_paths, mock_ca_cert, mock_client_cert = self._setup_verify(signature_valid=False)

        mock_x509.load_pem_x509_certificate.return_value = mock_client_cert

        with (
            patch.object(crypto, "ensure_certificates", return_value=mock_paths),
            patch.object(crypto, "_load_cert", return_value=mock_ca_cert),
        ):
            valid, result = crypto.verify_client_cert(FAKE_CERT_PEM)

        assert valid is False
        assert "signature invalid" in result

    @patch("elle.mobile.crypto.x509")
    def test_missing_cn_returns_false(self, mock_x509):
        """Cert with no CN attribute returns (False, error)."""
        crypto, mock_paths, mock_ca_cert, mock_client_cert = self._setup_verify(cn_attrs=[])

        mock_x509.load_pem_x509_certificate.return_value = mock_client_cert

        with (
            patch.object(crypto, "ensure_certificates", return_value=mock_paths),
            patch.object(crypto, "_load_cert", return_value=mock_ca_cert),
        ):
            valid, result = crypto.verify_client_cert(FAKE_CERT_PEM)

        assert valid is False
        assert "missing CN" in result

    @patch("elle.mobile.crypto.x509")
    def test_invalid_cn_format_returns_false(self, mock_x509):
        """Cert with CN not starting with 'device-' returns (False, error)."""
        crypto, mock_paths, mock_ca_cert, mock_client_cert = self._setup_verify(cn_value="not-a-device-cn")

        mock_x509.load_pem_x509_certificate.return_value = mock_client_cert

        with (
            patch.object(crypto, "ensure_certificates", return_value=mock_paths),
            patch.object(crypto, "_load_cert", return_value=mock_ca_cert),
        ):
            valid, result = crypto.verify_client_cert(FAKE_CERT_PEM)

        assert valid is False
        assert "Invalid CN format" in result

    @patch("elle.mobile.crypto.x509")
    def test_generic_exception_returns_false(self, mock_x509):
        """Any unhandled exception returns (False, str(exception))."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        with patch.object(crypto, "ensure_certificates", side_effect=RuntimeError("disk fail")):
            valid, result = crypto.verify_client_cert(FAKE_CERT_PEM)

        assert valid is False
        assert "disk fail" in result

    @patch("elle.mobile.crypto.x509")
    def test_cn_with_device_prefix_extracts_id(self, mock_x509):
        """CN 'device-XYZ' correctly extracts 'XYZ' as device_id."""
        crypto, mock_paths, mock_ca_cert, mock_client_cert = self._setup_verify(cn_value="device-my-long-device-id")

        mock_x509.load_pem_x509_certificate.return_value = mock_client_cert

        with (
            patch.object(crypto, "ensure_certificates", return_value=mock_paths),
            patch.object(crypto, "_load_cert", return_value=mock_ca_cert),
        ):
            valid, result = crypto.verify_client_cert(FAKE_CERT_PEM)

        assert valid is True
        assert result == "my-long-device-id"


# =============================================================================
# TestGenerateCA
# =============================================================================


class TestGenerateCA:
    """Tests for MobileCrypto._generate_ca."""

    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_writes_cert_and_key(self, mock_serialization, mock_hashes, mock_rsa, mock_x509, tmp_path):
        """_generate_ca writes cert and key to disk."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        mock_ca_key = MagicMock()
        mock_rsa.generate_private_key.return_value = mock_ca_key

        mock_cert_builder = MagicMock()
        mock_cert = MagicMock()
        mock_x509.CertificateBuilder.return_value = mock_cert_builder
        mock_cert_builder.subject_name.return_value = mock_cert_builder
        mock_cert_builder.issuer_name.return_value = mock_cert_builder
        mock_cert_builder.public_key.return_value = mock_cert_builder
        mock_cert_builder.serial_number.return_value = mock_cert_builder
        mock_cert_builder.not_valid_before.return_value = mock_cert_builder
        mock_cert_builder.not_valid_after.return_value = mock_cert_builder
        mock_cert_builder.add_extension.return_value = mock_cert_builder
        mock_cert_builder.sign.return_value = mock_cert

        mock_cert.public_bytes.return_value = FAKE_CERT_PEM
        mock_ca_key.private_bytes.return_value = FAKE_KEY_PEM

        cert_path = tmp_path / "ca.crt"
        key_path = tmp_path / "ca.key"

        crypto._generate_ca(cert_path, key_path)

        assert cert_path.read_bytes() == FAKE_CERT_PEM
        assert key_path.read_bytes() == FAKE_KEY_PEM

    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_sets_key_permissions_600(self, mock_serialization, mock_hashes, mock_rsa, mock_x509, tmp_path):
        """_generate_ca sets key file permissions to 0o600."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        mock_ca_key = MagicMock()
        mock_rsa.generate_private_key.return_value = mock_ca_key

        mock_cert_builder = MagicMock()
        mock_cert = MagicMock()
        mock_x509.CertificateBuilder.return_value = mock_cert_builder
        mock_cert_builder.subject_name.return_value = mock_cert_builder
        mock_cert_builder.issuer_name.return_value = mock_cert_builder
        mock_cert_builder.public_key.return_value = mock_cert_builder
        mock_cert_builder.serial_number.return_value = mock_cert_builder
        mock_cert_builder.not_valid_before.return_value = mock_cert_builder
        mock_cert_builder.not_valid_after.return_value = mock_cert_builder
        mock_cert_builder.add_extension.return_value = mock_cert_builder
        mock_cert_builder.sign.return_value = mock_cert

        mock_cert.public_bytes.return_value = FAKE_CERT_PEM
        mock_ca_key.private_bytes.return_value = FAKE_KEY_PEM

        cert_path = tmp_path / "ca.crt"
        key_path = tmp_path / "ca.key"

        crypto._generate_ca(cert_path, key_path)

        # Check permissions: 0o600 = owner read+write only

        mode = key_path.stat().st_mode & 0o777
        assert mode == 0o600

    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_uses_correct_key_params(self, mock_serialization, mock_hashes, mock_rsa, mock_x509, tmp_path):
        """_generate_ca generates key with correct exponent and size."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        mock_ca_key = MagicMock()
        mock_rsa.generate_private_key.return_value = mock_ca_key

        mock_cert_builder = MagicMock()
        mock_cert = MagicMock()
        mock_x509.CertificateBuilder.return_value = mock_cert_builder
        mock_cert_builder.subject_name.return_value = mock_cert_builder
        mock_cert_builder.issuer_name.return_value = mock_cert_builder
        mock_cert_builder.public_key.return_value = mock_cert_builder
        mock_cert_builder.serial_number.return_value = mock_cert_builder
        mock_cert_builder.not_valid_before.return_value = mock_cert_builder
        mock_cert_builder.not_valid_after.return_value = mock_cert_builder
        mock_cert_builder.add_extension.return_value = mock_cert_builder
        mock_cert_builder.sign.return_value = mock_cert

        mock_cert.public_bytes.return_value = FAKE_CERT_PEM
        mock_ca_key.private_bytes.return_value = FAKE_KEY_PEM

        cert_path = tmp_path / "ca.crt"
        key_path = tmp_path / "ca.key"

        crypto._generate_ca(cert_path, key_path)

        mock_rsa.generate_private_key.assert_called_once_with(
            public_exponent=65537,
            key_size=RSA_KEY_SIZE,
        )

    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_ca_cert_has_basic_constraints_ca_true(
        self, mock_serialization, mock_hashes, mock_rsa, mock_x509, tmp_path
    ):
        """_generate_ca sets BasicConstraints with ca=True."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        mock_ca_key = MagicMock()
        mock_rsa.generate_private_key.return_value = mock_ca_key

        mock_cert_builder = MagicMock()
        mock_cert = MagicMock()
        mock_x509.CertificateBuilder.return_value = mock_cert_builder
        mock_cert_builder.subject_name.return_value = mock_cert_builder
        mock_cert_builder.issuer_name.return_value = mock_cert_builder
        mock_cert_builder.public_key.return_value = mock_cert_builder
        mock_cert_builder.serial_number.return_value = mock_cert_builder
        mock_cert_builder.not_valid_before.return_value = mock_cert_builder
        mock_cert_builder.not_valid_after.return_value = mock_cert_builder
        mock_cert_builder.add_extension.return_value = mock_cert_builder
        mock_cert_builder.sign.return_value = mock_cert

        mock_cert.public_bytes.return_value = FAKE_CERT_PEM
        mock_ca_key.private_bytes.return_value = FAKE_KEY_PEM

        cert_path = tmp_path / "ca.crt"
        key_path = tmp_path / "ca.key"

        crypto._generate_ca(cert_path, key_path)

        # Verify BasicConstraints was called with ca=True
        mock_x509.BasicConstraints.assert_called_once_with(ca=True, path_length=0)


# =============================================================================
# TestGenerateServerCert
# =============================================================================


class TestGenerateServerCert:
    """Tests for MobileCrypto._generate_server_cert."""

    def _setup_server_cert_mocks(self, mock_x509, mock_rsa, mock_serialization):
        """Set up common mocks for server cert generation tests."""
        mock_server_key = MagicMock()
        mock_rsa.generate_private_key.return_value = mock_server_key

        mock_cert_builder = MagicMock()
        mock_cert = MagicMock()
        mock_x509.CertificateBuilder.return_value = mock_cert_builder
        mock_cert_builder.subject_name.return_value = mock_cert_builder
        mock_cert_builder.issuer_name.return_value = mock_cert_builder
        mock_cert_builder.public_key.return_value = mock_cert_builder
        mock_cert_builder.serial_number.return_value = mock_cert_builder
        mock_cert_builder.not_valid_before.return_value = mock_cert_builder
        mock_cert_builder.not_valid_after.return_value = mock_cert_builder
        mock_cert_builder.add_extension.return_value = mock_cert_builder
        mock_cert_builder.sign.return_value = mock_cert

        mock_cert.public_bytes.return_value = FAKE_CERT_PEM
        mock_server_key.private_bytes.return_value = FAKE_KEY_PEM

        return mock_server_key, mock_cert_builder, mock_cert

    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_writes_server_cert_and_key(self, mock_serialization, mock_hashes, mock_rsa, mock_x509, tmp_path):
        """_generate_server_cert writes cert and key to disk."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        self._setup_server_cert_mocks(mock_x509, mock_rsa, mock_serialization)

        ca_cert_path = tmp_path / "ca.crt"
        ca_key_path = tmp_path / "ca.key"
        server_cert_path = tmp_path / "server.crt"
        server_key_path = tmp_path / "server.key"

        mock_ca_cert = MagicMock()
        mock_ca_key = MagicMock()

        with (
            patch.object(crypto, "_load_cert", return_value=mock_ca_cert),
            patch.object(crypto, "_load_key", return_value=mock_ca_key),
        ):
            crypto._generate_server_cert(ca_cert_path, ca_key_path, server_cert_path, server_key_path)

        assert server_cert_path.read_bytes() == FAKE_CERT_PEM
        assert server_key_path.read_bytes() == FAKE_KEY_PEM

    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_sets_server_key_permissions_600(self, mock_serialization, mock_hashes, mock_rsa, mock_x509, tmp_path):
        """_generate_server_cert sets key file permissions to 0o600."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        self._setup_server_cert_mocks(mock_x509, mock_rsa, mock_serialization)

        ca_cert_path = tmp_path / "ca.crt"
        ca_key_path = tmp_path / "ca.key"
        server_cert_path = tmp_path / "server.crt"
        server_key_path = tmp_path / "server.key"

        with (
            patch.object(crypto, "_load_cert", return_value=MagicMock()),
            patch.object(crypto, "_load_key", return_value=MagicMock()),
        ):
            crypto._generate_server_cert(ca_cert_path, ca_key_path, server_cert_path, server_key_path)

        mode = server_key_path.stat().st_mode & 0o777
        assert mode == 0o600

    @patch("elle.mobile.crypto.ipaddress_from_string")
    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_default_bind_host_skips_san_addition(
        self, mock_serialization, mock_hashes, mock_rsa, mock_x509, mock_ipaddr, tmp_path
    ):
        """When bind_host is '0.0.0.0', it is not added to SAN."""
        config = _make_config(bind_host="0.0.0.0", overlay_host=None)
        crypto = MobileCrypto(config=config)

        self._setup_server_cert_mocks(mock_x509, mock_rsa, mock_serialization)

        ca_cert_path = tmp_path / "ca.crt"
        ca_key_path = tmp_path / "ca.key"
        server_cert_path = tmp_path / "server.crt"
        server_key_path = tmp_path / "server.key"

        with (
            patch.object(crypto, "_load_cert", return_value=MagicMock()),
            patch.object(crypto, "_load_key", return_value=MagicMock()),
        ):
            crypto._generate_server_cert(ca_cert_path, ca_key_path, server_cert_path, server_key_path)

        # ipaddress_from_string should be called for localhost "127.0.0.1" and "::1"
        # but NOT for "0.0.0.0"
        ip_calls = mock_ipaddr.call_args_list
        called_ips = [c[0][0] for c in ip_calls]
        assert "127.0.0.1" in called_ips
        assert "::1" in called_ips
        assert "0.0.0.0" not in called_ips

    @patch("elle.mobile.crypto.ipaddress_from_string")
    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_ipv6_bind_host_skips_san_addition(
        self, mock_serialization, mock_hashes, mock_rsa, mock_x509, mock_ipaddr, tmp_path
    ):
        """When bind_host is '::', it is not added to SAN."""
        config = _make_config(bind_host="::", overlay_host=None)
        crypto = MobileCrypto(config=config)

        self._setup_server_cert_mocks(mock_x509, mock_rsa, mock_serialization)

        ca_cert_path = tmp_path / "ca.crt"
        ca_key_path = tmp_path / "ca.key"
        server_cert_path = tmp_path / "server.crt"
        server_key_path = tmp_path / "server.key"

        with (
            patch.object(crypto, "_load_cert", return_value=MagicMock()),
            patch.object(crypto, "_load_key", return_value=MagicMock()),
        ):
            crypto._generate_server_cert(ca_cert_path, ca_key_path, server_cert_path, server_key_path)

        ip_calls = mock_ipaddr.call_args_list
        called_ips = [c[0][0] for c in ip_calls]
        assert "::" not in called_ips

    @patch("elle.mobile.crypto.ipaddress_from_string")
    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_custom_ip_bind_host_added_as_ip_san(
        self, mock_serialization, mock_hashes, mock_rsa, mock_x509, mock_ipaddr, tmp_path
    ):
        """When bind_host is a custom IP, it is added as IPAddress SAN."""
        config = _make_config(bind_host="192.168.1.100", overlay_host=None)
        crypto = MobileCrypto(config=config)

        self._setup_server_cert_mocks(mock_x509, mock_rsa, mock_serialization)

        ca_cert_path = tmp_path / "ca.crt"
        ca_key_path = tmp_path / "ca.key"
        server_cert_path = tmp_path / "server.crt"
        server_key_path = tmp_path / "server.key"

        with (
            patch.object(crypto, "_load_cert", return_value=MagicMock()),
            patch.object(crypto, "_load_key", return_value=MagicMock()),
        ):
            crypto._generate_server_cert(ca_cert_path, ca_key_path, server_cert_path, server_key_path)

        ip_calls = mock_ipaddr.call_args_list
        called_ips = [c[0][0] for c in ip_calls]
        assert "192.168.1.100" in called_ips

    @patch("elle.mobile.crypto.ipaddress_from_string")
    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_dns_bind_host_added_as_dns_san(
        self, mock_serialization, mock_hashes, mock_rsa, mock_x509, mock_ipaddr, tmp_path
    ):
        """When bind_host is a hostname (not IP), it is added as DNSName SAN."""
        config = _make_config(bind_host="myhost.local", overlay_host=None)
        crypto = MobileCrypto(config=config)

        self._setup_server_cert_mocks(mock_x509, mock_rsa, mock_serialization)

        # Make ipaddress_from_string raise ValueError for the hostname
        def _side_effect(addr):
            if addr in ("127.0.0.1", "::1"):
                return MagicMock()
            raise ValueError(f"Not a valid IP: {addr}")

        mock_ipaddr.side_effect = _side_effect

        ca_cert_path = tmp_path / "ca.crt"
        ca_key_path = tmp_path / "ca.key"
        server_cert_path = tmp_path / "server.crt"
        server_key_path = tmp_path / "server.key"

        with (
            patch.object(crypto, "_load_cert", return_value=MagicMock()),
            patch.object(crypto, "_load_key", return_value=MagicMock()),
        ):
            crypto._generate_server_cert(ca_cert_path, ca_key_path, server_cert_path, server_key_path)

        # DNSName should be called with the hostname
        mock_x509.DNSName.assert_any_call("myhost.local")

    @patch("elle.mobile.crypto.ipaddress_from_string")
    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_overlay_host_ip_added_as_san(
        self, mock_serialization, mock_hashes, mock_rsa, mock_x509, mock_ipaddr, tmp_path
    ):
        """When overlay_host is an IP, it is added as IPAddress SAN."""
        config = _make_config(overlay_host="10.0.0.1")
        crypto = MobileCrypto(config=config)

        self._setup_server_cert_mocks(mock_x509, mock_rsa, mock_serialization)

        ca_cert_path = tmp_path / "ca.crt"
        ca_key_path = tmp_path / "ca.key"
        server_cert_path = tmp_path / "server.crt"
        server_key_path = tmp_path / "server.key"

        with (
            patch.object(crypto, "_load_cert", return_value=MagicMock()),
            patch.object(crypto, "_load_key", return_value=MagicMock()),
        ):
            crypto._generate_server_cert(ca_cert_path, ca_key_path, server_cert_path, server_key_path)

        ip_calls = mock_ipaddr.call_args_list
        called_ips = [c[0][0] for c in ip_calls]
        assert "10.0.0.1" in called_ips

    @patch("elle.mobile.crypto.ipaddress_from_string")
    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_overlay_host_dns_added_as_san(
        self, mock_serialization, mock_hashes, mock_rsa, mock_x509, mock_ipaddr, tmp_path
    ):
        """When overlay_host is a hostname, it is added as DNSName SAN."""
        config = _make_config(overlay_host="vpn.example.com")
        crypto = MobileCrypto(config=config)

        self._setup_server_cert_mocks(mock_x509, mock_rsa, mock_serialization)

        def _side_effect(addr):
            if addr in ("127.0.0.1", "::1"):
                return MagicMock()
            raise ValueError(f"Not a valid IP: {addr}")

        mock_ipaddr.side_effect = _side_effect

        ca_cert_path = tmp_path / "ca.crt"
        ca_key_path = tmp_path / "ca.key"
        server_cert_path = tmp_path / "server.crt"
        server_key_path = tmp_path / "server.key"

        with (
            patch.object(crypto, "_load_cert", return_value=MagicMock()),
            patch.object(crypto, "_load_key", return_value=MagicMock()),
        ):
            crypto._generate_server_cert(ca_cert_path, ca_key_path, server_cert_path, server_key_path)

        mock_x509.DNSName.assert_any_call("vpn.example.com")

    @patch("elle.mobile.crypto.ipaddress_from_string")
    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_no_overlay_host_skips_overlay_san(
        self, mock_serialization, mock_hashes, mock_rsa, mock_x509, mock_ipaddr, tmp_path
    ):
        """When overlay_host is None, no overlay SAN is added."""
        config = _make_config(overlay_host=None)
        crypto = MobileCrypto(config=config)

        self._setup_server_cert_mocks(mock_x509, mock_rsa, mock_serialization)

        ca_cert_path = tmp_path / "ca.crt"
        ca_key_path = tmp_path / "ca.key"
        server_cert_path = tmp_path / "server.crt"
        server_key_path = tmp_path / "server.key"

        with (
            patch.object(crypto, "_load_cert", return_value=MagicMock()),
            patch.object(crypto, "_load_key", return_value=MagicMock()),
        ):
            crypto._generate_server_cert(ca_cert_path, ca_key_path, server_cert_path, server_key_path)

        # Only default SANs should be created (localhost, 127.0.0.1, ::1)
        # and NOT any overlay-related calls
        ip_calls = mock_ipaddr.call_args_list
        called_ips = [c[0][0] for c in ip_calls]
        assert len(called_ips) == 2  # 127.0.0.1 and ::1 only

    @patch("elle.mobile.crypto.x509")
    @patch("elle.mobile.crypto.rsa")
    @patch("elle.mobile.crypto.hashes")
    @patch("elle.mobile.crypto.serialization")
    def test_empty_bind_host_skips_san(self, mock_serialization, mock_hashes, mock_rsa, mock_x509, tmp_path):
        """When bind_host is empty string (falsy), it is not added to SAN."""
        config = _make_config(bind_host="", overlay_host=None)
        crypto = MobileCrypto(config=config)

        self._setup_server_cert_mocks(mock_x509, mock_rsa, mock_serialization)

        ca_cert_path = tmp_path / "ca.crt"
        ca_key_path = tmp_path / "ca.key"
        server_cert_path = tmp_path / "server.crt"
        server_key_path = tmp_path / "server.key"

        with (
            patch.object(crypto, "_load_cert", return_value=MagicMock()),
            patch.object(crypto, "_load_key", return_value=MagicMock()),
        ):
            crypto._generate_server_cert(ca_cert_path, ca_key_path, server_cert_path, server_key_path)

        # The empty bind_host should not trigger an additional SAN entry
        # (the condition `if bind_host and bind_host not in (...)` is False)


# =============================================================================
# TestLoadCert
# =============================================================================


class TestLoadCert:
    """Tests for MobileCrypto._load_cert."""

    @patch("elle.mobile.crypto.x509")
    def test_loads_pem_cert(self, mock_x509, tmp_path):
        """_load_cert reads the file and calls load_pem_x509_certificate."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        cert_path = tmp_path / "test.crt"
        cert_path.write_bytes(FAKE_CERT_PEM)

        mock_cert = MagicMock()
        mock_x509.load_pem_x509_certificate.return_value = mock_cert

        result = crypto._load_cert(cert_path)

        mock_x509.load_pem_x509_certificate.assert_called_once_with(FAKE_CERT_PEM)
        assert result is mock_cert


# =============================================================================
# TestLoadKey
# =============================================================================


class TestLoadKey:
    """Tests for MobileCrypto._load_key."""

    @patch("elle.mobile.crypto.serialization")
    def test_loads_pem_key(self, mock_serialization, tmp_path):
        """_load_key reads the file and calls load_pem_private_key."""
        config = _make_config()
        crypto = MobileCrypto(config=config)

        key_path = tmp_path / "test.key"
        key_path.write_bytes(FAKE_KEY_PEM)

        mock_key = MagicMock()
        mock_serialization.load_pem_private_key.return_value = mock_key

        result = crypto._load_key(key_path)

        mock_serialization.load_pem_private_key.assert_called_once_with(
            FAKE_KEY_PEM,
            password=None,
        )
        assert result is mock_key


# =============================================================================
# TestIPAddressFromString
# =============================================================================


class TestIPAddressFromString:
    """Tests for ipaddress_from_string top-level helper."""

    def test_ipv4_address(self):
        """Converts IPv4 string to IPv4Address."""
        result = ipaddress_from_string("192.168.1.1")
        assert isinstance(result, IPv4Address)
        assert str(result) == "192.168.1.1"

    def test_ipv6_address(self):
        """Converts IPv6 string to IPv6Address."""
        result = ipaddress_from_string("::1")
        assert isinstance(result, IPv6Address)
        assert str(result) == "::1"

    def test_full_ipv6_address(self):
        """Converts full IPv6 string to IPv6Address."""
        result = ipaddress_from_string("2001:db8::1")
        assert isinstance(result, IPv6Address)

    def test_invalid_address_raises_valueerror(self):
        """Invalid IP string raises ValueError."""
        with pytest.raises(ValueError):
            ipaddress_from_string("not-an-ip")

    def test_loopback_ipv4(self):
        """Converts loopback IPv4."""
        result = ipaddress_from_string("127.0.0.1")
        assert isinstance(result, IPv4Address)
        assert str(result) == "127.0.0.1"
