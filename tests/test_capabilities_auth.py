"""Tests for authentication capabilities."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pydantic
import pytest

from elle.capabilities.core.auth import (
    MobileCertsCapability,
    MobileCertsInput,
    SessionTokenCapability,
    SessionTokenInput,
)

# Shared patch target prefix for session token functions
_ST = "elle.daemon.api.session_token"

# ---------------------------------------------------------------------------
# Ensure elle.mobile.crypto is importable even when 'cryptography' is absent.
# The real module imports 'cryptography' at the top level, which may not be
# installed in the test environment.  We inject a lightweight mock module
# into sys.modules so that ``patch("elle.mobile.crypto.MobileCrypto")`` can
# resolve the dotted path.  The patch decorator then replaces MobileCrypto
# with a per-test MagicMock as usual.
# ---------------------------------------------------------------------------
import elle.mobile  # noqa: E402

if "elle.mobile.crypto" not in sys.modules:
    _crypto_stub = MagicMock()
    sys.modules["elle.mobile.crypto"] = _crypto_stub
    elle.mobile.crypto = _crypto_stub  # Required for patch() dotted-path resolution


# =============================================================================
# Mock helpers
# =============================================================================


def _mock_mobile_paths():
    """Create a mock CertPaths object returned by ensure_certificates."""
    paths = MagicMock()
    paths.ca_cert = Path("/var/lib/elle/mobile/ca.crt")
    paths.server_cert = Path("/var/lib/elle/mobile/server.crt")
    paths.server_key = Path("/var/lib/elle/mobile/server.key")
    return paths


def _mock_client_cert():
    """Create a mock ClientCert object returned by generate_client_certificate."""
    client = MagicMock()
    client.cert_pem = b"-----BEGIN CERTIFICATE-----\nFAKECERT\n-----END CERTIFICATE-----\n"
    client.key_pem = b"-----BEGIN PRIVATE KEY-----\nFAKEKEY\n-----END PRIVATE KEY-----\n"
    client.fingerprint = "AA:BB:CC:DD:EE:FF:00:11"
    return client


# =============================================================================
# TestSessionTokenGenerate
# =============================================================================


class TestSessionTokenGenerate:
    """Tests for auth.session_token generate action."""

    def setup_method(self):
        self.cap = SessionTokenCapability()

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.write_token_file")
    @patch(f"{_ST}.generate_session_token")
    def test_generate_writes_file(self, mock_gen, mock_write, mock_path):
        """Generate token writes file and returns token."""
        mock_gen.return_value = "tok_abc123"
        mock_write.return_value = Path("/var/lib/elle/session.token")
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="generate")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.action == "generate"
        assert result.output.token == "tok_abc123"
        assert result.output.token_path == "/var/lib/elle/session.token"
        mock_gen.assert_called_once()
        mock_write.assert_called_once_with("tok_abc123", Path("/var/lib/elle/session.token"))

    @patch(f"{_ST}.write_token_file")
    @patch(f"{_ST}.generate_session_token")
    def test_generate_custom_path(self, mock_gen, mock_write):
        """Generate token honours custom_path."""
        mock_gen.return_value = "tok_custom"
        mock_write.return_value = Path("/tmp/my_token")

        inp = SessionTokenInput(action="generate", custom_path="/tmp/my_token")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.token_path == "/tmp/my_token"
        mock_write.assert_called_once_with("tok_custom", Path("/tmp/my_token"))

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.write_token_file")
    @patch(f"{_ST}.generate_session_token")
    def test_generate_file_permissions_check(self, mock_gen, mock_write, mock_path):
        """Generate records file_write side effect for permissions audit."""
        mock_gen.return_value = "tok_perm"
        mock_write.return_value = Path("/var/lib/elle/session.token")
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="generate")
        result = self.cap.run(inp)

        assert result.success is True
        assert len(result.side_effects_applied) == 1
        effect = result.side_effects_applied[0]
        assert effect.kind == "file_write"
        assert "session.token" in effect.target
        assert result.evidence.files_modified == ("/var/lib/elle/session.token",)

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.write_token_file")
    @patch(f"{_ST}.generate_session_token")
    def test_generate_overwrite_existing_token(self, mock_gen, mock_write, mock_path):
        """Generating a new token overwrites any existing one."""
        mock_gen.return_value = "tok_new"
        mock_write.return_value = Path("/var/lib/elle/session.token")
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="generate")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.token == "tok_new"
        mock_write.assert_called_once()

    @patch(f"{_ST}.write_token_file")
    @patch(f"{_ST}.generate_session_token")
    def test_generate_directory_creation_custom_path(self, mock_gen, mock_write):
        """Generate with deep custom path passes full path to write_token_file."""
        mock_gen.return_value = "tok_deep"
        mock_write.return_value = Path("/opt/elle/tokens/deep/session.token")

        inp = SessionTokenInput(
            action="generate",
            custom_path="/opt/elle/tokens/deep/session.token",
        )
        result = self.cap.run(inp)

        assert result.success is True
        mock_write.assert_called_once_with("tok_deep", Path("/opt/elle/tokens/deep/session.token"))


# =============================================================================
# TestSessionTokenRead
# =============================================================================


class TestSessionTokenRead:
    """Tests for auth.session_token read action."""

    def setup_method(self):
        self.cap = SessionTokenCapability()

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.read_token_file")
    def test_read_existing_token(self, mock_read, mock_path):
        """Read returns the stored token."""
        mock_read.return_value = "tok_existing"
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="read")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.action == "read"
        assert result.output.token == "tok_existing"
        assert result.error is None

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.read_token_file")
    def test_read_missing_file_returns_none(self, mock_read, mock_path):
        """When file is missing read_token_file returns None; result success is False."""
        mock_read.return_value = None
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="read")
        result = self.cap.run(inp)

        assert result.success is False
        assert result.output.token is None
        assert result.error == "Token file not found or unreadable"

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.read_token_file")
    def test_read_empty_file_handling(self, mock_read, mock_path):
        """Empty string from read is truthy, so still succeeds."""
        mock_read.return_value = ""
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="read")
        result = self.cap.run(inp)

        # Empty string is not None so success is True per the source logic
        assert result.output.token == ""
        assert result.output.success is True

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.read_token_file")
    def test_read_corrupted_unreadable_file(self, mock_read, mock_path):
        """Corrupted/unreadable file causes read_token_file to return None."""
        mock_read.return_value = None
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="read")
        result = self.cap.run(inp)

        assert result.success is False
        assert result.output.success is False
        assert result.error is not None


# =============================================================================
# TestSessionTokenDelete
# =============================================================================


class TestSessionTokenDelete:
    """Tests for auth.session_token delete action."""

    def setup_method(self):
        self.cap = SessionTokenCapability()

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.delete_token_file")
    def test_delete_existing_file(self, mock_delete, mock_path):
        """Deleting an existing file returns success and records side effect."""
        mock_delete.return_value = True
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="delete")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.action == "delete"
        assert result.output.success is True
        assert len(result.side_effects_applied) == 1
        assert result.side_effects_applied[0].kind == "file_delete"

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.delete_token_file")
    def test_delete_missing_file_still_success(self, mock_delete, mock_path):
        """Deleting a missing file still returns success=True (idempotent)."""
        mock_delete.return_value = False
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="delete")
        result = self.cap.run(inp)

        # The capability returns success=True regardless; output.success carries deletion status
        assert result.success is True
        assert result.output.success is False
        # No side effects recorded when nothing was deleted
        assert len(result.side_effects_applied) == 0

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.delete_token_file")
    def test_delete_permission_denied(self, mock_delete, mock_path):
        """PermissionError during delete is caught and reported."""
        mock_delete.side_effect = PermissionError("Permission denied")
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="delete")
        result = self.cap.run(inp)

        assert result.success is False
        assert "Permission denied" in result.error

    @patch(f"{_ST}.delete_token_file")
    def test_delete_custom_path(self, mock_delete):
        """Delete with custom_path targets the right file."""
        mock_delete.return_value = True

        inp = SessionTokenInput(action="delete", custom_path="/tmp/my_session.token")
        result = self.cap.run(inp)

        assert result.success is True
        mock_delete.assert_called_once_with(Path("/tmp/my_session.token"))
        assert result.output.token_path == "/tmp/my_session.token"


# =============================================================================
# TestSessionTokenValidate
# =============================================================================


class TestSessionTokenValidate:
    """Tests for auth.session_token validate action."""

    def setup_method(self):
        self.cap = SessionTokenCapability()

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.validate_token")
    @patch(f"{_ST}.read_token_file")
    def test_valid_token_matches(self, mock_read, mock_validate, mock_path):
        """Validating a correct token returns valid=True."""
        mock_read.return_value = "tok_stored"
        mock_validate.return_value = True
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="validate", token="tok_stored")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.valid is True
        mock_validate.assert_called_once_with("tok_stored", "tok_stored")

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.validate_token")
    @patch(f"{_ST}.read_token_file")
    def test_expired_wrong_token(self, mock_read, mock_validate, mock_path):
        """Validating a wrong token returns valid=False."""
        mock_read.return_value = "tok_stored"
        mock_validate.return_value = False
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="validate", token="tok_wrong")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.valid is False
        mock_validate.assert_called_once_with("tok_wrong", "tok_stored")

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.validate_token")
    @patch(f"{_ST}.read_token_file")
    def test_malformed_token(self, mock_read, mock_validate, mock_path):
        """Malformed token still goes through validate_token logic."""
        mock_read.return_value = "tok_stored"
        mock_validate.return_value = False
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="validate", token="not-a-valid-format")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.valid is False

    @patch(f"{_ST}.get_token_path")
    def test_empty_token_error(self, mock_path):
        """Validate with empty token string returns error 'Token required'."""
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="validate", token="")
        result = self.cap.run(inp)

        assert result.success is False
        assert "Token required" in result.error

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.read_token_file")
    def test_no_stored_token_to_validate_against(self, mock_read, mock_path):
        """Validate when no stored token exists returns error."""
        mock_read.return_value = None
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="validate", token="tok_candidate")
        result = self.cap.run(inp)

        assert result.success is False
        assert "No stored token to validate against" in result.error


# =============================================================================
# TestMobileCertsCapability
# =============================================================================


class TestMobileCertsCapability:
    """Tests for auth.mobile_certs capability."""

    def setup_method(self):
        self.cap = MobileCertsCapability()

    @patch("elle.mobile.crypto.MobileCrypto")
    def test_ensure_creates_paths(self, mock_crypto_cls):
        """Ensure action creates CA and server cert paths."""
        mock_crypto = mock_crypto_cls.return_value
        mock_paths = _mock_mobile_paths()
        mock_crypto.ensure_certificates.return_value = mock_paths

        inp = MobileCertsInput(action="ensure")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.action == "ensure"
        assert result.output.ca_cert_path == "/var/lib/elle/mobile/ca.crt"
        assert result.output.server_cert_path == "/var/lib/elle/mobile/server.crt"
        mock_crypto.ensure_certificates.assert_called_once()

    @patch("elle.mobile.crypto.MobileCrypto")
    def test_generate_client_needs_device_id_and_name(self, mock_crypto_cls):
        """generate_client fails without device_id and device_name."""
        inp = MobileCertsInput(action="generate_client")
        result = self.cap.run(inp)

        assert result.success is False
        assert "device_id and device_name required" in result.error

    @patch("elle.mobile.crypto.MobileCrypto")
    def test_generate_client_success(self, mock_crypto_cls):
        """generate_client with device_id and device_name succeeds."""
        mock_crypto = mock_crypto_cls.return_value
        mock_client = _mock_client_cert()
        mock_crypto.generate_client_certificate.return_value = mock_client

        inp = MobileCertsInput(
            action="generate_client",
            device_id="dev-001",
            device_name="My Phone",
        )
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.device_id == "dev-001"
        assert result.output.fingerprint == "AA:BB:CC:DD:EE:FF:00:11"
        assert "FAKECERT" in result.output.client_cert_pem
        mock_crypto.generate_client_certificate.assert_called_once_with("dev-001", "My Phone")

    @patch("elle.mobile.crypto.MobileCrypto")
    def test_verify_client_needs_cert_pem(self, mock_crypto_cls):
        """verify_client fails without cert_pem."""
        inp = MobileCertsInput(action="verify_client")
        result = self.cap.run(inp)

        assert result.success is False
        assert "cert_pem required" in result.error

    @patch("elle.mobile.crypto.MobileCrypto")
    def test_get_fingerprint_returns_fingerprint(self, mock_crypto_cls):
        """get_fingerprint returns server certificate fingerprint."""
        mock_crypto = mock_crypto_cls.return_value
        mock_crypto.get_server_fingerprint.return_value = "AA:BB:CC:DD"

        inp = MobileCertsInput(action="get_fingerprint")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.fingerprint == "AA:BB:CC:DD"
        mock_crypto.get_server_fingerprint.assert_called_once()

    @patch("elle.mobile.crypto.MobileCrypto")
    def test_rotate_server_deletes_and_regenerates(self, mock_crypto_cls):
        """rotate_server deletes existing certs and regenerates."""
        mock_crypto = mock_crypto_cls.return_value
        mock_paths = _mock_mobile_paths()

        # Make the path objects mock-friendly for exists/unlink
        mock_server_cert = MagicMock(spec=Path)
        mock_server_cert.exists.return_value = True
        mock_server_cert.__str__ = lambda s: "/var/lib/elle/mobile/server.crt"
        mock_paths.server_cert = mock_server_cert

        mock_server_key = MagicMock(spec=Path)
        mock_server_key.exists.return_value = True
        mock_server_key.__str__ = lambda s: "/var/lib/elle/mobile/server.key"
        mock_paths.server_key = mock_server_key

        new_paths = _mock_mobile_paths()
        mock_crypto.ensure_certificates.side_effect = [mock_paths, new_paths]
        mock_crypto.get_server_fingerprint.return_value = "NEW:FP"

        inp = MobileCertsInput(action="rotate_server")
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.action == "rotate_server"
        assert result.output.fingerprint == "NEW:FP"
        # Verify old certs were deleted
        mock_server_cert.unlink.assert_called_once()
        mock_server_key.unlink.assert_called_once()
        # ensure_certificates called twice: once to get paths, once to regenerate
        assert mock_crypto.ensure_certificates.call_count == 2

    def test_import_error_when_cryptography_unavailable(self):
        """ImportError from mobile.crypto is caught and reported."""
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "elle.mobile.crypto":
                raise ImportError("No module named 'cryptography'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            inp = MobileCertsInput(action="ensure")
            cap = MobileCertsCapability()
            result = cap.run(inp)

            assert result.success is False
            assert "not available" in result.error or "No module" in result.error


# =============================================================================
# TestAuthInputValidation
# =============================================================================


class TestAuthInputValidation:
    """Tests for Pydantic input validation."""

    def test_invalid_action_rejected_by_pydantic(self):
        """Invalid action value is rejected by Pydantic Literal validation."""
        with pytest.raises(pydantic.ValidationError):
            SessionTokenInput(action="nonexistent_action")

    def test_session_token_path_traversal_still_works(self):
        """Path traversal in custom_path is accepted (it is just a path string)."""
        inp = SessionTokenInput(
            action="generate",
            custom_path="../../etc/shadow",
        )
        assert inp.custom_path == "../../etc/shadow"

    def test_mobile_certs_unknown_action(self):
        """Unknown action for MobileCertsInput is rejected by Pydantic."""
        with pytest.raises(pydantic.ValidationError):
            MobileCertsInput(action="unknown_action")


# =============================================================================
# TestAuthDryRun
# =============================================================================


class TestAuthDryRun:
    """Tests for dry_run preview behaviour."""

    def setup_method(self):
        self.session_cap = SessionTokenCapability()
        self.mobile_cap = MobileCertsCapability()

    @patch(f"{_ST}.get_token_path")
    def test_dry_run_generate_shows_file_path(self, mock_path):
        """Dry run for generate shows the token file path."""
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="generate")
        result = self.session_cap.dry_run(inp)

        assert result.is_valid is True
        assert "/var/lib/elle/session.token" in result.preview_text
        assert len(result.would_modify) > 0
        assert str(Path("/var/lib/elle/session.token")) in result.would_modify

    @patch(f"{_ST}.get_token_path")
    def test_dry_run_delete_requires_confirmation(self, mock_path):
        """Dry run for delete requires user confirmation."""
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="delete")
        result = self.session_cap.dry_run(inp)

        assert result.requires_confirmation is True
        assert result.estimated_risk == "medium"

    @patch(f"{_ST}.get_token_path")
    def test_dry_run_read_is_low_risk(self, mock_path):
        """Dry run for read is low risk and does not modify anything."""
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="read")
        result = self.session_cap.dry_run(inp)

        assert result.estimated_risk == "low"
        assert len(result.would_modify) == 0
        assert result.requires_confirmation is False


# =============================================================================
# TestSessionTokenSpec  (covers line 84)
# =============================================================================


class TestSessionTokenSpec:
    """Tests for SessionTokenCapability.spec property."""

    def test_spec_returns_correct_capability_spec(self):
        """Accessing spec property returns a valid CapabilitySpec for session_token."""
        cap = SessionTokenCapability()
        spec = cap.spec

        assert spec.name == "auth.session_token"
        assert spec.domain == "auth"
        assert spec.risk == "medium"
        assert spec.trust_level == "core"
        assert spec.version == "1.0.0"
        assert spec.requires_privilege is False
        assert spec.idempotent is False
        assert len(spec.side_effects) == 1
        assert spec.side_effects[0].kind == "file_write"
        assert spec.side_effects[0].target == "session.token"


# =============================================================================
# TestSessionTokenRunException  (covers line 234 else branch)
# =============================================================================


class TestSessionTokenRunException:
    """Tests for exception handling in SessionTokenCapability.run."""

    def setup_method(self):
        self.cap = SessionTokenCapability()

    @patch(f"{_ST}.get_token_path")
    @patch(f"{_ST}.generate_session_token")
    def test_run_exception_during_generate(self, mock_gen, mock_path):
        """Exception during generate is caught and returns failure."""
        mock_gen.side_effect = OSError("Disk full")
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="generate")
        result = self.cap.run(inp)

        assert result.success is False
        assert "Disk full" in result.error


# =============================================================================
# TestSessionTokenVerify  (covers lines 249-270)
# =============================================================================


class TestSessionTokenVerify:
    """Tests for SessionTokenCapability.verify method."""

    def setup_method(self):
        self.cap = SessionTokenCapability()

    @patch(f"{_ST}.read_token_file")
    @patch(f"{_ST}.get_token_path")
    def test_verify_generate_token_exists(self, mock_path, mock_read):
        """Verify after generate passes when token file exists."""
        mock_path.return_value = Path("/var/lib/elle/session.token")
        mock_read.return_value = "tok_abc123"

        inp = SessionTokenInput(action="generate")
        result = self.cap.verify(inp)

        assert result.passed is True
        assert result.actual_state == {"token_exists": True}
        assert result.expected_state == {"token_exists": True}

    @patch(f"{_ST}.read_token_file")
    @patch(f"{_ST}.get_token_path")
    def test_verify_generate_token_missing(self, mock_path, mock_read):
        """Verify after generate fails when token file is missing."""
        mock_path.return_value = Path("/var/lib/elle/session.token")
        mock_read.return_value = None

        inp = SessionTokenInput(action="generate")
        result = self.cap.verify(inp)

        assert result.passed is False
        assert result.actual_state == {"token_exists": False}

    @patch(f"{_ST}.get_token_path")
    def test_verify_delete_file_gone(self, mock_path):
        """Verify after delete passes when token file is gone."""
        mock_path.return_value = Path("/tmp/nonexistent_token_file_xyz")

        inp = SessionTokenInput(action="delete")
        result = self.cap.verify(inp)

        assert result.passed is True
        assert result.actual_state == {"token_exists": False}
        assert result.expected_state == {"token_exists": False}

    @patch(f"{_ST}.get_token_path")
    def test_verify_delete_file_still_exists(self, mock_path, tmp_path):
        """Verify after delete fails when token file still exists."""
        token_file = tmp_path / "session.token"
        token_file.write_text("leftover")
        mock_path.return_value = token_file

        inp = SessionTokenInput(action="delete")
        result = self.cap.verify(inp)

        assert result.passed is False
        assert result.actual_state == {"token_exists": True}

    @patch(f"{_ST}.get_token_path")
    def test_verify_read_action_passes_trivially(self, mock_path):
        """Verify for read/validate actions returns trivially passed."""
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="read")
        result = self.cap.verify(inp)

        assert result.passed is True

    @patch(f"{_ST}.get_token_path")
    def test_verify_validate_action_passes_trivially(self, mock_path):
        """Verify for validate action returns trivially passed."""
        mock_path.return_value = Path("/var/lib/elle/session.token")

        inp = SessionTokenInput(action="validate", token="tok_test")
        result = self.cap.verify(inp)

        assert result.passed is True

    @patch(f"{_ST}.read_token_file")
    def test_verify_generate_custom_path(self, mock_read):
        """Verify with custom_path uses the custom path."""
        mock_read.return_value = "tok_custom"

        inp = SessionTokenInput(action="generate", custom_path="/tmp/custom.token")
        result = self.cap.verify(inp)

        assert result.passed is True
        mock_read.assert_called_once_with(Path("/tmp/custom.token"))


# =============================================================================
# TestMobileCertsSpec  (covers line 338)
# =============================================================================


class TestMobileCertsSpec:
    """Tests for MobileCertsCapability.spec property."""

    def test_spec_returns_correct_capability_spec(self):
        """Accessing spec property returns a valid CapabilitySpec for mobile_certs."""
        cap = MobileCertsCapability()
        spec = cap.spec

        assert spec.name == "auth.mobile_certs"
        assert spec.domain == "auth"
        assert spec.risk == "high"
        assert spec.trust_level == "core"
        assert spec.version == "1.0.0"
        assert spec.requires_privilege is False
        assert spec.idempotent is False
        assert len(spec.side_effects) == 1
        assert spec.side_effects[0].kind == "file_write"
        assert "mobile" in spec.side_effects[0].target
        assert spec.dependencies == ("cryptography",)


# =============================================================================
# TestMobileCertsDryRun  (covers lines 362-378)
# =============================================================================


class TestMobileCertsDryRun:
    """Tests for MobileCertsCapability.dry_run method."""

    def setup_method(self):
        self.cap = MobileCertsCapability()

    def test_dry_run_ensure_high_risk(self):
        """Dry run for ensure action is high risk and lists cert files."""
        inp = MobileCertsInput(action="ensure")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.estimated_risk == "high"
        assert "ca.crt" in result.would_modify
        assert "server.crt" in result.would_modify
        assert result.requires_confirmation is False

    def test_dry_run_generate_client_low_risk(self):
        """Dry run for generate_client is low risk with no modifications."""
        inp = MobileCertsInput(action="generate_client", device_id="dev-001")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.estimated_risk == "low"
        assert len(result.would_modify) == 0
        assert "dev-001" in result.preview_text

    def test_dry_run_verify_client_low_risk(self):
        """Dry run for verify_client is low risk with no modifications."""
        inp = MobileCertsInput(action="verify_client")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.estimated_risk == "low"
        assert len(result.would_modify) == 0

    def test_dry_run_get_fingerprint_low_risk(self):
        """Dry run for get_fingerprint is low risk with no modifications."""
        inp = MobileCertsInput(action="get_fingerprint")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.estimated_risk == "low"
        assert len(result.would_modify) == 0

    def test_dry_run_rotate_server_high_risk_requires_confirmation(self):
        """Dry run for rotate_server is high risk and requires confirmation."""
        inp = MobileCertsInput(action="rotate_server")
        result = self.cap.dry_run(inp)

        assert result.is_valid is True
        assert result.estimated_risk == "high"
        assert result.requires_confirmation is True
        assert "server.crt" in result.would_modify
        assert "server.key" in result.would_modify


# =============================================================================
# TestMobileCertsVerifyClient  (covers lines 458-459)
# =============================================================================


class TestMobileCertsVerifyClient:
    """Tests for verify_client action with actual cert_pem."""

    def setup_method(self):
        self.cap = MobileCertsCapability()

    @patch("elle.mobile.crypto.MobileCrypto")
    def test_verify_client_valid_cert(self, mock_crypto_cls):
        """verify_client with valid cert returns verified=True and device_id."""
        mock_crypto = mock_crypto_cls.return_value
        mock_crypto.verify_client_cert.return_value = (True, "dev-001")

        inp = MobileCertsInput(
            action="verify_client",
            cert_pem="-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n",
        )
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.verified is True
        assert result.output.device_id == "dev-001"
        assert result.output.error_message is None
        mock_crypto.verify_client_cert.assert_called_once()

    @patch("elle.mobile.crypto.MobileCrypto")
    def test_verify_client_invalid_cert(self, mock_crypto_cls):
        """verify_client with invalid cert returns verified=False and error."""
        mock_crypto = mock_crypto_cls.return_value
        mock_crypto.verify_client_cert.return_value = (False, "Certificate expired")

        inp = MobileCertsInput(
            action="verify_client",
            cert_pem="-----BEGIN CERTIFICATE-----\nBAD\n-----END CERTIFICATE-----\n",
        )
        result = self.cap.run(inp)

        assert result.success is True
        assert result.output.verified is False
        assert result.output.device_id is None
        assert result.output.error_message == "Certificate expired"


# =============================================================================
# TestMobileCertsRotateServerBranches  (covers lines 486->488, 488->491)
# =============================================================================


class TestMobileCertsRotateServerBranches:
    """Tests for branch conditions in rotate_server where certs may not exist."""

    def setup_method(self):
        self.cap = MobileCertsCapability()

    @patch("elle.mobile.crypto.MobileCrypto")
    def test_rotate_server_cert_missing_key_exists(self, mock_crypto_cls):
        """rotate_server when server cert is missing but key exists."""
        mock_crypto = mock_crypto_cls.return_value
        mock_paths = _mock_mobile_paths()

        mock_server_cert = MagicMock(spec=Path)
        mock_server_cert.exists.return_value = False
        mock_server_cert.__str__ = lambda s: "/var/lib/elle/mobile/server.crt"
        mock_paths.server_cert = mock_server_cert

        mock_server_key = MagicMock(spec=Path)
        mock_server_key.exists.return_value = True
        mock_server_key.__str__ = lambda s: "/var/lib/elle/mobile/server.key"
        mock_paths.server_key = mock_server_key

        new_paths = _mock_mobile_paths()
        mock_crypto.ensure_certificates.side_effect = [mock_paths, new_paths]
        mock_crypto.get_server_fingerprint.return_value = "NEW:FP:01"

        inp = MobileCertsInput(action="rotate_server")
        result = self.cap.run(inp)

        assert result.success is True
        mock_server_cert.unlink.assert_not_called()
        mock_server_key.unlink.assert_called_once()

    @patch("elle.mobile.crypto.MobileCrypto")
    def test_rotate_server_both_missing(self, mock_crypto_cls):
        """rotate_server when both server cert and key are missing."""
        mock_crypto = mock_crypto_cls.return_value
        mock_paths = _mock_mobile_paths()

        mock_server_cert = MagicMock(spec=Path)
        mock_server_cert.exists.return_value = False
        mock_server_cert.__str__ = lambda s: "/var/lib/elle/mobile/server.crt"
        mock_paths.server_cert = mock_server_cert

        mock_server_key = MagicMock(spec=Path)
        mock_server_key.exists.return_value = False
        mock_server_key.__str__ = lambda s: "/var/lib/elle/mobile/server.key"
        mock_paths.server_key = mock_server_key

        new_paths = _mock_mobile_paths()
        mock_crypto.ensure_certificates.side_effect = [mock_paths, new_paths]
        mock_crypto.get_server_fingerprint.return_value = "NEW:FP:02"

        inp = MobileCertsInput(action="rotate_server")
        result = self.cap.run(inp)

        assert result.success is True
        mock_server_cert.unlink.assert_not_called()
        mock_server_key.unlink.assert_not_called()

    @patch("elle.mobile.crypto.MobileCrypto")
    def test_rotate_server_cert_exists_key_missing(self, mock_crypto_cls):
        """rotate_server when server cert exists but key is missing."""
        mock_crypto = mock_crypto_cls.return_value
        mock_paths = _mock_mobile_paths()

        mock_server_cert = MagicMock(spec=Path)
        mock_server_cert.exists.return_value = True
        mock_server_cert.__str__ = lambda s: "/var/lib/elle/mobile/server.crt"
        mock_paths.server_cert = mock_server_cert

        mock_server_key = MagicMock(spec=Path)
        mock_server_key.exists.return_value = False
        mock_server_key.__str__ = lambda s: "/var/lib/elle/mobile/server.key"
        mock_paths.server_key = mock_server_key

        new_paths = _mock_mobile_paths()
        mock_crypto.ensure_certificates.side_effect = [mock_paths, new_paths]
        mock_crypto.get_server_fingerprint.return_value = "NEW:FP:03"

        inp = MobileCertsInput(action="rotate_server")
        result = self.cap.run(inp)

        assert result.success is True
        mock_server_cert.unlink.assert_called_once()
        mock_server_key.unlink.assert_not_called()


# =============================================================================
# TestMobileCertsRunExceptions  (covers lines 520, 532-533)
# =============================================================================


class TestMobileCertsRunExceptions:
    """Tests for exception handling in MobileCertsCapability.run."""

    def setup_method(self):
        self.cap = MobileCertsCapability()

    @patch("elle.mobile.crypto.MobileCrypto")
    def test_generic_exception_during_ensure(self, mock_crypto_cls):
        """Generic exception during ensure is caught and reported."""
        mock_crypto = mock_crypto_cls.return_value
        mock_crypto.ensure_certificates.side_effect = RuntimeError("Certificate generation failed")

        inp = MobileCertsInput(action="ensure")
        result = self.cap.run(inp)

        assert result.success is False
        assert "Certificate generation failed" in result.error

    @patch("elle.mobile.crypto.MobileCrypto")
    def test_generic_exception_during_get_fingerprint(self, mock_crypto_cls):
        """Generic exception during get_fingerprint is caught and reported."""
        mock_crypto = mock_crypto_cls.return_value
        mock_crypto.get_server_fingerprint.side_effect = FileNotFoundError("No cert file")

        inp = MobileCertsInput(action="get_fingerprint")
        result = self.cap.run(inp)

        assert result.success is False
        assert "No cert file" in result.error

    @patch("elle.mobile.crypto.MobileCrypto")
    def test_generic_exception_during_generate_client(self, mock_crypto_cls):
        """Generic exception during generate_client is caught and reported."""
        mock_crypto = mock_crypto_cls.return_value
        mock_crypto.generate_client_certificate.side_effect = ValueError("Invalid device")

        inp = MobileCertsInput(
            action="generate_client",
            device_id="dev-bad",
            device_name="Bad Device",
        )
        result = self.cap.run(inp)

        assert result.success is False
        assert "Invalid device" in result.error
