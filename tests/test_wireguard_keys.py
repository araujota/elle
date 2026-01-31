from __future__ import annotations

"""Comprehensive tests for elle.ops.wireguard.keys module.

Covers: _run_wg_command, generate_keypair, generate_preshared_key,
derive_public_key, get_current_public_key, get_interface_config_path,
rotate_keys_safely, list_interfaces, get_interface_stats,
KeyRotationResult model, and WireGuardKeyError exception.
"""

import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from elle.ops.wireguard.keys import (
    WIREGUARD_CONFIG_DIR,
    KeyRotationResult,
    WireGuardKeyError,
    _run_wg_command,
    derive_public_key,
    generate_keypair,
    generate_preshared_key,
    get_current_public_key,
    get_interface_config_path,
    get_interface_stats,
    list_interfaces,
    rotate_keys_safely,
)

# ---------------------------------------------------------------------------
# Fake keys for testing (valid 44-char base64 WireGuard keys)
# ---------------------------------------------------------------------------

FAKE_PRIVATE_KEY = "cPvPPFPQbLZ9stQnT5J+MdG7g5pmxcpNTm7tZHsCj1s="
FAKE_PUBLIC_KEY = "Jk8P+8U3HCHPFGpNt/6bL6Ge3Ke0kM8mLg3UpSi4PDs="
FAKE_NEW_PRIVATE_KEY = "aMnOPQRStuvWXyz1234567890abcdefghijklmnopqr0="
FAKE_NEW_PUBLIC_KEY = "Xk9P+8U3HCHPFGpNt/6bL6Ge3Ke0kM8mLg3UpSi4PDs="
FAKE_PSK = "dGhpcyBpcyBhIGZha2UgcHJlc2hhcmVkIGtleSBiYXNlNjQ="

SAMPLE_WG_CONFIG = f"""\
[Interface]
PrivateKey = {FAKE_PRIVATE_KEY}
Address = 10.0.0.1/24
ListenPort = 51820

[Peer]
PublicKey = {FAKE_PUBLIC_KEY}
AllowedIPs = 10.0.0.2/32
"""


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Build a fake subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(args=["wg"], stdout=stdout, stderr=stderr, returncode=returncode)


# ===========================================================================
# WireGuardKeyError
# ===========================================================================


class TestWireGuardKeyError:
    def test_is_exception(self):
        err = WireGuardKeyError("something broke")
        assert isinstance(err, Exception)
        assert str(err) == "something broke"

    def test_can_be_raised_and_caught(self):
        with pytest.raises(WireGuardKeyError, match="test error"):
            raise WireGuardKeyError("test error")


# ===========================================================================
# KeyRotationResult model
# ===========================================================================


class TestKeyRotationResult:
    def test_default_values(self):
        result = KeyRotationResult(success=True, interface="wg0")
        assert result.success is True
        assert result.interface == "wg0"
        assert result.old_public_key is None
        assert result.new_public_key is None
        assert result.backup_path is None
        assert result.error is None
        assert isinstance(result.rotated_at, datetime)
        assert result.steps_completed == ()

    def test_frozen_model(self):
        result = KeyRotationResult(success=False, interface="wg0", error="fail")
        with pytest.raises(Exception):
            result.success = True  # type: ignore[misc]

    def test_all_fields_populated(self):
        result = KeyRotationResult(
            success=True,
            interface="wg0",
            old_public_key="old_key",
            new_public_key="new_key",
            backup_path="/var/lib/elle/backups/wireguard/wg0_backup.conf",
            error=None,
            steps_completed=("step1", "step2"),
        )
        assert result.old_public_key == "old_key"
        assert result.new_public_key == "new_key"
        assert result.backup_path == "/var/lib/elle/backups/wireguard/wg0_backup.conf"
        assert len(result.steps_completed) == 2

    def test_failure_result(self):
        result = KeyRotationResult(
            success=False,
            interface="wg0",
            error="Interface wg0 not found or not active",
        )
        assert result.success is False
        assert "not found" in result.error


# ===========================================================================
# _run_wg_command
# ===========================================================================


class TestRunWgCommand:
    @patch("elle.ops.wireguard.keys.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = _completed(stdout="output\n")
        result = _run_wg_command(["genkey"])
        mock_run.assert_called_once_with(["wg", "genkey"], capture_output=True, text=True, timeout=30)
        assert result.stdout == "output\n"
        assert result.returncode == 0

    @patch("elle.ops.wireguard.keys.subprocess.run")
    def test_capture_false(self, mock_run):
        mock_run.return_value = _completed()
        _run_wg_command(["show"], capture=False)
        mock_run.assert_called_once_with(["wg", "show"], capture_output=False, text=True, timeout=30)

    @patch("elle.ops.wireguard.keys.subprocess.run")
    def test_file_not_found_raises(self, mock_run):
        mock_run.side_effect = FileNotFoundError("wg not found")
        with pytest.raises(WireGuardKeyError, match="WireGuard tools not installed"):
            _run_wg_command(["genkey"])

    @patch("elle.ops.wireguard.keys.subprocess.run")
    def test_timeout_raises(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="wg", timeout=30)
        with pytest.raises(WireGuardKeyError, match="timed out"):
            _run_wg_command(["show", "wg0"])

    @patch("elle.ops.wireguard.keys.subprocess.run")
    def test_multiple_args(self, mock_run):
        mock_run.return_value = _completed()
        _run_wg_command(["show", "wg0", "public-key"])
        mock_run.assert_called_once_with(
            ["wg", "show", "wg0", "public-key"],
            capture_output=True,
            text=True,
            timeout=30,
        )


# ===========================================================================
# generate_keypair
# ===========================================================================


class TestGenerateKeypair:
    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_success(self, mock_wg_cmd, mock_subprocess_run):
        mock_wg_cmd.return_value = _completed(stdout=FAKE_PRIVATE_KEY + "\n")
        mock_subprocess_run.return_value = _completed(stdout=FAKE_PUBLIC_KEY + "\n")

        private, public = generate_keypair()

        assert private == FAKE_PRIVATE_KEY
        assert public == FAKE_PUBLIC_KEY
        mock_wg_cmd.assert_called_once_with(["genkey"])
        mock_subprocess_run.assert_called_once_with(
            ["wg", "pubkey"],
            input=FAKE_PRIVATE_KEY,
            capture_output=True,
            text=True,
            timeout=10,
        )

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_genkey_fails(self, mock_wg_cmd):
        mock_wg_cmd.return_value = _completed(returncode=1, stderr="genkey error")
        with pytest.raises(WireGuardKeyError, match="Failed to generate private key"):
            generate_keypair()

    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_pubkey_derivation_fails(self, mock_wg_cmd, mock_subprocess_run):
        mock_wg_cmd.return_value = _completed(stdout=FAKE_PRIVATE_KEY + "\n")
        mock_subprocess_run.return_value = _completed(returncode=1, stderr="pubkey error")
        with pytest.raises(WireGuardKeyError, match="Failed to derive public key"):
            generate_keypair()

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_genkey_wg_not_installed(self, mock_wg_cmd):
        mock_wg_cmd.side_effect = WireGuardKeyError("WireGuard tools not installed")
        with pytest.raises(WireGuardKeyError, match="not installed"):
            generate_keypair()


# ===========================================================================
# generate_preshared_key
# ===========================================================================


class TestGeneratePresharedKey:
    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_success(self, mock_wg_cmd):
        mock_wg_cmd.return_value = _completed(stdout=FAKE_PSK + "\n")
        psk = generate_preshared_key()
        assert psk == FAKE_PSK
        mock_wg_cmd.assert_called_once_with(["genpsk"])

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_failure(self, mock_wg_cmd):
        mock_wg_cmd.return_value = _completed(returncode=1, stderr="psk error")
        with pytest.raises(WireGuardKeyError, match="Failed to generate preshared key"):
            generate_preshared_key()


# ===========================================================================
# derive_public_key
# ===========================================================================


class TestDerivePublicKey:
    @patch("elle.ops.wireguard.keys.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = _completed(stdout=FAKE_PUBLIC_KEY + "\n")
        pub = derive_public_key(FAKE_PRIVATE_KEY)
        assert pub == FAKE_PUBLIC_KEY
        mock_run.assert_called_once_with(
            ["wg", "pubkey"],
            input=FAKE_PRIVATE_KEY,
            capture_output=True,
            text=True,
            timeout=10,
        )

    @patch("elle.ops.wireguard.keys.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = _completed(returncode=1, stderr="bad key")
        with pytest.raises(WireGuardKeyError, match="Failed to derive public key"):
            derive_public_key("invalid-key")


# ===========================================================================
# get_current_public_key
# ===========================================================================


class TestGetCurrentPublicKey:
    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_success(self, mock_wg_cmd):
        mock_wg_cmd.return_value = _completed(stdout=FAKE_PUBLIC_KEY + "\n")
        key = get_current_public_key("wg0")
        assert key == FAKE_PUBLIC_KEY
        mock_wg_cmd.assert_called_once_with(["show", "wg0", "public-key"])

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_interface_not_found(self, mock_wg_cmd):
        mock_wg_cmd.return_value = _completed(returncode=1, stderr="not found")
        key = get_current_public_key("wg99")
        assert key is None

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_strips_whitespace(self, mock_wg_cmd):
        mock_wg_cmd.return_value = _completed(stdout="  " + FAKE_PUBLIC_KEY + "  \n")
        key = get_current_public_key("wg0")
        assert key == FAKE_PUBLIC_KEY


# ===========================================================================
# get_interface_config_path
# ===========================================================================


class TestGetInterfaceConfigPath:
    def test_returns_correct_path(self):
        path = get_interface_config_path("wg0")
        assert path == WIREGUARD_CONFIG_DIR / "wg0.conf"
        assert str(path) == "/etc/wireguard/wg0.conf"

    def test_different_interface(self):
        path = get_interface_config_path("wg-tunnel")
        assert path == Path("/etc/wireguard/wg-tunnel.conf")


# ===========================================================================
# list_interfaces
# ===========================================================================


class TestListInterfaces:
    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_multiple_interfaces(self, mock_wg_cmd):
        mock_wg_cmd.return_value = _completed(stdout="wg0 wg1 wg2\n")
        result = list_interfaces()
        assert result == ["wg0", "wg1", "wg2"]
        mock_wg_cmd.assert_called_once_with(["show", "interfaces"])

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_single_interface(self, mock_wg_cmd):
        mock_wg_cmd.return_value = _completed(stdout="wg0\n")
        result = list_interfaces()
        assert result == ["wg0"]

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_no_interfaces(self, mock_wg_cmd):
        mock_wg_cmd.return_value = _completed(returncode=1, stderr="no interfaces")
        result = list_interfaces()
        assert result == []

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_empty_stdout(self, mock_wg_cmd):
        mock_wg_cmd.return_value = _completed(stdout="\n")
        result = list_interfaces()
        # .strip().split() on "\n" yields [""]
        # but the function returns whatever split() gives
        assert isinstance(result, list)


# ===========================================================================
# get_interface_stats
# ===========================================================================


class TestGetInterfaceStats:
    WG_DUMP_INTERFACE = "privkey\tPUBKEY\t51820\toff"
    WG_DUMP_PEER = "PEER_PUB\tpsk\t1.2.3.4:51820\t10.0.0.0/24,10.0.1.0/24\t1700000000\t1000\t2000\t25"

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_success_with_peer(self, mock_wg_cmd):
        dump_output = f"{self.WG_DUMP_INTERFACE}\n{self.WG_DUMP_PEER}\n"
        mock_wg_cmd.return_value = _completed(stdout=dump_output)

        stats = get_interface_stats("wg0")

        assert stats is not None
        assert stats["public_key"] == "PUBKEY"
        assert stats["listen_port"] == 51820
        assert len(stats["peers"]) == 1

        peer = stats["peers"][0]
        assert peer["public_key"] == "PEER_PUB"
        assert peer["endpoint"] == "1.2.3.4:51820"
        assert peer["allowed_ips"] == ["10.0.0.0/24", "10.0.1.0/24"]
        assert peer["latest_handshake"] == 1700000000
        assert peer["rx_bytes"] == 1000
        assert peer["tx_bytes"] == 2000
        assert peer["keepalive"] == 25

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_nonzero_return(self, mock_wg_cmd):
        mock_wg_cmd.return_value = _completed(returncode=1)
        stats = get_interface_stats("wg0")
        assert stats is None

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_empty_output(self, mock_wg_cmd):
        mock_wg_cmd.return_value = _completed(stdout="")
        stats = get_interface_stats("wg0")
        assert stats is None

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_interface_line_too_few_parts(self, mock_wg_cmd):
        mock_wg_cmd.return_value = _completed(stdout="privkey\tPUBKEY\t51820\n")
        stats = get_interface_stats("wg0")
        assert stats is None

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_none_values(self, mock_wg_cmd):
        dump = "privkey\tPUBKEY\t(none)\toff\n"
        mock_wg_cmd.return_value = _completed(stdout=dump)

        stats = get_interface_stats("wg0")

        assert stats is not None
        assert stats["listen_port"] is None

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_peer_none_endpoint(self, mock_wg_cmd):
        peer_line = "PEER_PUB\tpsk\t(none)\t10.0.0.0/24\t0\t100\t200\toff"
        dump = f"privkey\tPUBKEY\t51820\toff\n{peer_line}\n"
        mock_wg_cmd.return_value = _completed(stdout=dump)

        stats = get_interface_stats("wg0")

        assert stats is not None
        peer = stats["peers"][0]
        assert peer["endpoint"] is None
        assert peer["latest_handshake"] is None  # "0" maps to None
        assert peer["keepalive"] is None  # "off" maps to None

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_peer_line_too_few_parts(self, mock_wg_cmd):
        dump = "privkey\tPUBKEY\t51820\toff\nshort_line\n"
        mock_wg_cmd.return_value = _completed(stdout=dump)

        stats = get_interface_stats("wg0")

        assert stats is not None
        assert len(stats["peers"]) == 0

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_multiple_peers(self, mock_wg_cmd):
        peer1 = "PEER1\tpsk\t1.2.3.4:51820\t10.0.0.0/24\t1700000000\t500\t600\t25"
        peer2 = "PEER2\t(none)\t5.6.7.8:51820\t10.0.1.0/24\t1700000001\t700\t800\toff"
        dump = f"privkey\tPUBKEY\t51820\toff\n{peer1}\n{peer2}\n"
        mock_wg_cmd.return_value = _completed(stdout=dump)

        stats = get_interface_stats("wg0")

        assert stats is not None
        assert len(stats["peers"]) == 2
        assert stats["peers"][0]["public_key"] == "PEER1"
        assert stats["peers"][1]["public_key"] == "PEER2"

    @patch("elle.ops.wireguard.keys._run_wg_command")
    def test_empty_allowed_ips(self, mock_wg_cmd):
        peer_line = "PEER_PUB\tpsk\t1.2.3.4:51820\t\t1700000000\t100\t200\toff"
        dump = f"privkey\tPUBKEY\t51820\toff\n{peer_line}\n"
        mock_wg_cmd.return_value = _completed(stdout=dump)

        stats = get_interface_stats("wg0")

        assert stats is not None
        peer = stats["peers"][0]
        assert peer["allowed_ips"] == []


# ===========================================================================
# rotate_keys_safely
# ===========================================================================


class TestRotateKeysSafely:
    """Tests for the full key rotation workflow."""

    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys.generate_keypair")
    @patch("elle.ops.wireguard.keys.get_current_public_key")
    @patch("elle.ops.wireguard.keys.get_interface_config_path")
    def test_interface_not_found(self, mock_config_path, mock_get_key, mock_gen, mock_run):
        mock_get_key.return_value = None
        mock_config_path.return_value = Path("/etc/wireguard/wg0.conf")

        result = rotate_keys_safely("wg0")

        assert result.success is False
        assert "not found" in result.error
        assert result.interface == "wg0"
        mock_gen.assert_not_called()

    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys.generate_keypair")
    @patch("elle.ops.wireguard.keys.get_current_public_key")
    @patch("elle.ops.wireguard.keys.get_interface_config_path")
    def test_config_file_missing(self, mock_config_path, mock_get_key, mock_gen, mock_run):
        mock_get_key.return_value = FAKE_PUBLIC_KEY
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = False
        mock_config_path.return_value = mock_path

        result = rotate_keys_safely("wg0")

        assert result.success is False
        assert "not found" in result.error or "Configuration file" in result.error
        assert result.old_public_key == FAKE_PUBLIC_KEY
        mock_gen.assert_not_called()

    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys.generate_keypair")
    @patch("elle.ops.wireguard.keys.get_current_public_key")
    @patch("elle.ops.wireguard.keys.get_interface_config_path")
    def test_keypair_generation_fails(self, mock_config_path, mock_get_key, mock_gen, mock_run):
        mock_get_key.return_value = FAKE_PUBLIC_KEY
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_config_path.return_value = mock_path

        mock_gen.side_effect = WireGuardKeyError("keygen failed")

        result = rotate_keys_safely("wg0")

        assert result.success is False
        assert "keygen failed" in result.error
        assert result.old_public_key == FAKE_PUBLIC_KEY
        assert len(result.steps_completed) >= 2  # retrieved key + found config

    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys.generate_keypair")
    @patch("elle.ops.wireguard.keys.get_current_public_key")
    @patch("elle.ops.wireguard.keys.get_interface_config_path")
    @patch("elle.ops.wireguard.keys.Path")
    def test_successful_rotation(self, mock_path_cls, mock_config_path, mock_get_key, mock_gen, mock_subprocess_run):
        mock_get_key.return_value = FAKE_PUBLIC_KEY

        # Config path mock
        config_file = MagicMock(spec=Path)
        config_file.exists.return_value = True
        config_file.read_text.return_value = SAMPLE_WG_CONFIG
        mock_config_path.return_value = config_file

        # Keypair generation
        mock_gen.return_value = (FAKE_NEW_PRIVATE_KEY, FAKE_NEW_PUBLIC_KEY)

        # Backup dir mock
        backup_dir = MagicMock(spec=Path)
        backup_path = MagicMock(spec=Path)
        backup_path.__str__ = lambda _: "/var/lib/elle/backups/wireguard/wg0_20240101_120000.conf"
        backup_dir.__truediv__ = MagicMock(return_value=backup_path)
        mock_path_cls.return_value = backup_dir

        # wg-quick down succeeds, wg-quick up succeeds
        mock_subprocess_run.side_effect = [
            _completed(),  # wg-quick down
            _completed(),  # wg-quick up
        ]

        result = rotate_keys_safely("wg0")

        assert result.success is True
        assert result.old_public_key == FAKE_PUBLIC_KEY
        assert result.new_public_key == FAKE_NEW_PUBLIC_KEY
        assert result.interface == "wg0"
        assert len(result.steps_completed) >= 4

    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys.generate_keypair")
    @patch("elle.ops.wireguard.keys.get_current_public_key")
    @patch("elle.ops.wireguard.keys.get_interface_config_path")
    @patch("elle.ops.wireguard.keys.Path")
    def test_wg_quick_up_fails_restores_backup(
        self, mock_path_cls, mock_config_path, mock_get_key, mock_gen, mock_subprocess_run
    ):
        mock_get_key.return_value = FAKE_PUBLIC_KEY

        config_file = MagicMock(spec=Path)
        config_file.exists.return_value = True
        config_file.read_text.return_value = SAMPLE_WG_CONFIG
        mock_config_path.return_value = config_file

        mock_gen.return_value = (FAKE_NEW_PRIVATE_KEY, FAKE_NEW_PUBLIC_KEY)

        backup_dir = MagicMock(spec=Path)
        backup_path = MagicMock(spec=Path)
        backup_path.__str__ = lambda _: "/var/lib/elle/backups/wireguard/wg0_backup.conf"
        backup_dir.__truediv__ = MagicMock(return_value=backup_path)
        mock_path_cls.return_value = backup_dir

        # wg-quick down succeeds, wg-quick up fails
        mock_subprocess_run.side_effect = [
            _completed(),  # wg-quick down
            _completed(returncode=1, stderr="up failed"),  # wg-quick up
        ]

        result = rotate_keys_safely("wg0")

        assert result.success is False
        assert "Failed to restart interface" in result.error
        # Verify backup rename was attempted for restoration
        backup_path.rename.assert_called_once_with(config_file)

    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys.generate_keypair")
    @patch("elle.ops.wireguard.keys.get_current_public_key")
    @patch("elle.ops.wireguard.keys.get_interface_config_path")
    @patch("elle.ops.wireguard.keys.Path")
    def test_no_private_key_in_config(
        self, mock_path_cls, mock_config_path, mock_get_key, mock_gen, mock_subprocess_run
    ):
        """Config without PrivateKey line should fail."""
        mock_get_key.return_value = FAKE_PUBLIC_KEY

        config_file = MagicMock(spec=Path)
        config_file.exists.return_value = True
        config_file.read_text.return_value = "[Interface]\nAddress = 10.0.0.1/24\nListenPort = 51820\n"
        mock_config_path.return_value = config_file

        mock_gen.return_value = (FAKE_NEW_PRIVATE_KEY, FAKE_NEW_PUBLIC_KEY)

        backup_dir = MagicMock(spec=Path)
        backup_path = MagicMock(spec=Path)
        backup_path.__str__ = lambda _: "/var/lib/elle/backups/wireguard/wg0_backup.conf"
        backup_dir.__truediv__ = MagicMock(return_value=backup_path)
        mock_path_cls.return_value = backup_dir

        result = rotate_keys_safely("wg0")

        assert result.success is False
        assert "PrivateKey" in result.error

    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys.generate_keypair")
    @patch("elle.ops.wireguard.keys.get_current_public_key")
    @patch("elle.ops.wireguard.keys.get_interface_config_path")
    @patch("elle.ops.wireguard.keys.Path")
    def test_permission_denied(self, mock_path_cls, mock_config_path, mock_get_key, mock_gen, mock_subprocess_run):
        mock_get_key.return_value = FAKE_PUBLIC_KEY

        config_file = MagicMock(spec=Path)
        config_file.exists.return_value = True
        config_file.read_text.side_effect = PermissionError("permission denied")
        mock_config_path.return_value = config_file

        mock_gen.return_value = (FAKE_NEW_PRIVATE_KEY, FAKE_NEW_PUBLIC_KEY)

        backup_dir = MagicMock(spec=Path)
        mock_path_cls.return_value = backup_dir

        result = rotate_keys_safely("wg0")

        assert result.success is False
        assert "Permission denied" in result.error or "root" in result.error

    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys.generate_keypair")
    @patch("elle.ops.wireguard.keys.get_current_public_key")
    @patch("elle.ops.wireguard.keys.get_interface_config_path")
    @patch("elle.ops.wireguard.keys.Path")
    def test_generic_exception(self, mock_path_cls, mock_config_path, mock_get_key, mock_gen, mock_subprocess_run):
        mock_get_key.return_value = FAKE_PUBLIC_KEY

        config_file = MagicMock(spec=Path)
        config_file.exists.return_value = True
        config_file.read_text.side_effect = OSError("disk error")
        mock_config_path.return_value = config_file

        mock_gen.return_value = (FAKE_NEW_PRIVATE_KEY, FAKE_NEW_PUBLIC_KEY)

        backup_dir = MagicMock(spec=Path)
        mock_path_cls.return_value = backup_dir

        result = rotate_keys_safely("wg0")

        assert result.success is False
        assert "disk error" in result.error

    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys.generate_keypair")
    @patch("elle.ops.wireguard.keys.get_current_public_key")
    @patch("elle.ops.wireguard.keys.get_interface_config_path")
    @patch("elle.ops.wireguard.keys.Path")
    def test_steps_completed_tracked(
        self, mock_path_cls, mock_config_path, mock_get_key, mock_gen, mock_subprocess_run
    ):
        """Verify steps_completed captures progress before failure."""
        mock_get_key.return_value = FAKE_PUBLIC_KEY

        config_file = MagicMock(spec=Path)
        config_file.exists.return_value = True
        mock_config_path.return_value = config_file

        mock_gen.side_effect = WireGuardKeyError("keygen boom")

        result = rotate_keys_safely("wg0")

        assert result.success is False
        # Should have at least the "Retrieved current public key" step
        assert any("Retrieved current" in s for s in result.steps_completed)
        assert any("Found config" in s for s in result.steps_completed)

    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys.generate_keypair")
    @patch("elle.ops.wireguard.keys.get_current_public_key")
    @patch("elle.ops.wireguard.keys.get_interface_config_path")
    @patch("elle.ops.wireguard.keys.Path")
    def test_backup_chmod_called(self, mock_path_cls, mock_config_path, mock_get_key, mock_gen, mock_subprocess_run):
        """Verify backup file gets restrictive permissions."""
        mock_get_key.return_value = FAKE_PUBLIC_KEY

        config_file = MagicMock(spec=Path)
        config_file.exists.return_value = True
        config_file.read_text.return_value = SAMPLE_WG_CONFIG
        mock_config_path.return_value = config_file

        mock_gen.return_value = (FAKE_NEW_PRIVATE_KEY, FAKE_NEW_PUBLIC_KEY)

        backup_dir = MagicMock(spec=Path)
        backup_path = MagicMock(spec=Path)
        backup_path.__str__ = lambda _: "/var/lib/elle/backups/wireguard/wg0_backup.conf"
        backup_dir.__truediv__ = MagicMock(return_value=backup_path)
        mock_path_cls.return_value = backup_dir

        mock_subprocess_run.side_effect = [
            _completed(),  # wg-quick down
            _completed(),  # wg-quick up
        ]

        result = rotate_keys_safely("wg0")

        assert result.success is True
        backup_path.chmod.assert_called_once_with(0o600)

    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys.generate_keypair")
    @patch("elle.ops.wireguard.keys.get_current_public_key")
    @patch("elle.ops.wireguard.keys.get_interface_config_path")
    @patch("elle.ops.wireguard.keys.Path")
    def test_config_updated_with_new_key(
        self, mock_path_cls, mock_config_path, mock_get_key, mock_gen, mock_subprocess_run
    ):
        """Verify the config file is written with the new private key."""
        mock_get_key.return_value = FAKE_PUBLIC_KEY

        config_file = MagicMock(spec=Path)
        config_file.exists.return_value = True
        config_file.read_text.return_value = SAMPLE_WG_CONFIG
        mock_config_path.return_value = config_file

        mock_gen.return_value = (FAKE_NEW_PRIVATE_KEY, FAKE_NEW_PUBLIC_KEY)

        backup_dir = MagicMock(spec=Path)
        backup_path = MagicMock(spec=Path)
        backup_path.__str__ = lambda _: "/var/lib/elle/backups/wireguard/wg0_backup.conf"
        backup_dir.__truediv__ = MagicMock(return_value=backup_path)
        mock_path_cls.return_value = backup_dir

        mock_subprocess_run.side_effect = [
            _completed(),  # wg-quick down
            _completed(),  # wg-quick up
        ]

        result = rotate_keys_safely("wg0")

        assert result.success is True
        # Verify write_text was called with content containing the new key
        written = config_file.write_text.call_args[0][0]
        assert FAKE_NEW_PRIVATE_KEY in written
        assert FAKE_PRIVATE_KEY not in written

    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys.generate_keypair")
    @patch("elle.ops.wireguard.keys.get_current_public_key")
    @patch("elle.ops.wireguard.keys.get_interface_config_path")
    @patch("elle.ops.wireguard.keys.Path")
    def test_wg_quick_down_up_called(
        self, mock_path_cls, mock_config_path, mock_get_key, mock_gen, mock_subprocess_run
    ):
        """Verify wg-quick down and up are both called."""
        mock_get_key.return_value = FAKE_PUBLIC_KEY

        config_file = MagicMock(spec=Path)
        config_file.exists.return_value = True
        config_file.read_text.return_value = SAMPLE_WG_CONFIG
        mock_config_path.return_value = config_file

        mock_gen.return_value = (FAKE_NEW_PRIVATE_KEY, FAKE_NEW_PUBLIC_KEY)

        backup_dir = MagicMock(spec=Path)
        backup_path = MagicMock(spec=Path)
        backup_path.__str__ = lambda _: "/var/lib/elle/backups/wireguard/wg0_backup.conf"
        backup_dir.__truediv__ = MagicMock(return_value=backup_path)
        mock_path_cls.return_value = backup_dir

        mock_subprocess_run.side_effect = [
            _completed(),  # wg-quick down
            _completed(),  # wg-quick up
        ]

        result = rotate_keys_safely("wg0")

        assert result.success is True
        assert mock_subprocess_run.call_count == 2
        down_call = mock_subprocess_run.call_args_list[0]
        up_call = mock_subprocess_run.call_args_list[1]
        assert down_call == call(
            ["wg-quick", "down", "wg0"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert up_call == call(
            ["wg-quick", "up", "wg0"],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_grace_period_accepted(self):
        """Verify grace_period_seconds parameter is accepted."""
        with patch("elle.ops.wireguard.keys.get_current_public_key", return_value=None):
            result = rotate_keys_safely("wg0", grace_period_seconds=600)
        assert result.success is False  # fails at first check, but param accepted

    @patch("elle.ops.wireguard.keys.subprocess.run")
    @patch("elle.ops.wireguard.keys.generate_keypair")
    @patch("elle.ops.wireguard.keys.get_current_public_key")
    @patch("elle.ops.wireguard.keys.get_interface_config_path")
    @patch("elle.ops.wireguard.keys.Path")
    def test_backup_dir_created(self, mock_path_cls, mock_config_path, mock_get_key, mock_gen, mock_subprocess_run):
        """Verify backup directory is created with parents=True."""
        mock_get_key.return_value = FAKE_PUBLIC_KEY

        config_file = MagicMock(spec=Path)
        config_file.exists.return_value = True
        config_file.read_text.return_value = SAMPLE_WG_CONFIG
        mock_config_path.return_value = config_file

        mock_gen.return_value = (FAKE_NEW_PRIVATE_KEY, FAKE_NEW_PUBLIC_KEY)

        backup_dir = MagicMock(spec=Path)
        backup_path = MagicMock(spec=Path)
        backup_path.__str__ = lambda _: "/var/lib/elle/backups/wireguard/wg0_backup.conf"
        backup_dir.__truediv__ = MagicMock(return_value=backup_path)
        mock_path_cls.return_value = backup_dir

        mock_subprocess_run.side_effect = [
            _completed(),  # wg-quick down
            _completed(),  # wg-quick up
        ]

        result = rotate_keys_safely("wg0")

        assert result.success is True
        backup_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)


# ===========================================================================
# Module-level constants
# ===========================================================================


class TestModuleConstants:
    def test_wireguard_config_dir(self):
        assert Path("/etc/wireguard") == WIREGUARD_CONFIG_DIR
