"""Tests for network operation capabilities."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pydantic
import pytest

from elle.capabilities.core.network import (
    NETWORK_CAPABILITIES,
    NetworkDiagnoseCapability,
    NetworkDiagnoseInput,
    NetworkListenersCapability,
    NetworkListenersInput,
    WireGuardGenerateKeyCapability,
    WireGuardGenerateKeyInput,
    WireGuardRestartCapability,
    WireGuardRestartInput,
    WireGuardRotateKeysCapability,
    WireGuardRotateKeysInput,
    WireGuardStatusCapability,
    WireGuardStatusInput,
    _interface_exists,
    _is_wireguard_available,
    _parse_wg_show,
    _run_command,
)

# Shared patch target prefix for module-level helpers
_NET = "elle.capabilities.core.network"


# =============================================================================
# Helper function tests
# =============================================================================


class TestRunCommand:
    """Tests for the _run_command helper."""

    @patch(f"{_NET}.subprocess.run")
    def test_run_command_passes_args(self, mock_run):
        """_run_command forwards cmd, timeout, and check to subprocess.run."""
        mock_run.return_value = subprocess.CompletedProcess(args=["echo", "hi"], returncode=0, stdout="hi\n", stderr="")
        result = _run_command(["echo", "hi"], timeout=10, check=True)

        mock_run.assert_called_once_with(
            ["echo", "hi"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        assert result.returncode == 0
        assert result.stdout == "hi\n"


class TestIsWireguardAvailable:
    """Tests for _is_wireguard_available helper."""

    @patch(f"{_NET}._run_command")
    def test_available_when_which_succeeds(self, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess(
            args=["which", "wg-quick"], returncode=0, stdout="/usr/bin/wg-quick", stderr=""
        )
        assert _is_wireguard_available() is True

    @patch(f"{_NET}._run_command")
    def test_unavailable_when_which_fails(self, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess(
            args=["which", "wg-quick"], returncode=1, stdout="", stderr=""
        )
        assert _is_wireguard_available() is False

    @patch(f"{_NET}._run_command", side_effect=subprocess.TimeoutExpired("which", 5))
    def test_unavailable_on_timeout(self, mock_cmd):
        assert _is_wireguard_available() is False

    @patch(f"{_NET}._run_command", side_effect=FileNotFoundError)
    def test_unavailable_on_file_not_found(self, mock_cmd):
        assert _is_wireguard_available() is False


class TestInterfaceExists:
    """Tests for _interface_exists helper."""

    @patch(f"{_NET}._run_command")
    def test_exists_when_ip_link_succeeds(self, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="wg0: ...", stderr="")
        assert _interface_exists("wg0") is True

    @patch(f"{_NET}._run_command")
    def test_not_exists_when_ip_link_fails(self, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not found")
        assert _interface_exists("wg0") is False

    @patch(f"{_NET}._run_command", side_effect=OSError("no ip"))
    def test_not_exists_on_exception(self, mock_cmd):
        assert _interface_exists("wg0") is False


class TestParseWgShow:
    """Tests for _parse_wg_show parser."""

    def test_parse_full_output(self):
        output = (
            "interface: wg0\n"
            "  public key: AAAA\n"
            "  private key: (hidden)\n"
            "  listening port: 51820\n"
            "\n"
            "peer: BBBB\n"
            "  endpoint: 1.2.3.4:51820\n"
            "  allowed ips: 10.0.0.0/24, 10.1.0.0/16\n"
            "  latest handshake: 1 minute, 23 seconds ago\n"
            "  transfer: 1.50GiB received, 300MiB sent\n"
        )
        data = _parse_wg_show(output)
        assert data["interface"] == "wg0"
        assert data["public_key"] == "AAAA"
        assert data["private_key"] == "(hidden)"
        assert data["listening_port"] == 51820
        assert len(data["peers"]) == 1
        peer = data["peers"][0]
        assert peer["public_key"] == "BBBB"
        assert peer["endpoint"] == "1.2.3.4:51820"
        assert peer["allowed_ips"] == ["10.0.0.0/24", "10.1.0.0/16"]
        assert peer["latest_handshake"] == "1 minute, 23 seconds ago"
        assert peer["transfer_rx"] == "1.50GiB"
        assert peer["transfer_tx"] == "300MiB"

    def test_parse_empty_output(self):
        data = _parse_wg_show("")
        assert data["interface"] is None
        assert data["peers"] == []

    def test_parse_multiple_peers(self):
        output = "interface: wg0\npeer: PEER1\n  endpoint: 1.1.1.1:51820\npeer: PEER2\n  endpoint: 2.2.2.2:51820\n"
        data = _parse_wg_show(output)
        assert len(data["peers"]) == 2
        assert data["peers"][0]["public_key"] == "PEER1"
        assert data["peers"][1]["public_key"] == "PEER2"

    def test_parse_invalid_listening_port(self):
        """Non-integer listening port is silently ignored."""
        output = "listening port: not-a-number\n"
        data = _parse_wg_show(output)
        assert data["listening_port"] is None


# =============================================================================
# Input model validation tests
# =============================================================================


class TestInputValidation:
    """Tests for Pydantic model validation on network inputs."""

    def test_wireguard_restart_default_values(self):
        inp = WireGuardRestartInput()
        assert inp.interface == "wg0"
        assert inp.verify_handshake is True
        assert inp.handshake_timeout_sec == 30

    def test_wireguard_restart_invalid_interface_name(self):
        with pytest.raises(pydantic.ValidationError):
            WireGuardRestartInput(interface="eth0")

    def test_wireguard_restart_timeout_too_low(self):
        with pytest.raises(pydantic.ValidationError):
            WireGuardRestartInput(handshake_timeout_sec=1)

    def test_wireguard_restart_timeout_too_high(self):
        with pytest.raises(pydantic.ValidationError):
            WireGuardRestartInput(handshake_timeout_sec=999)

    def test_wireguard_status_default_values(self):
        inp = WireGuardStatusInput()
        assert inp.interface == "wg0"
        assert inp.check_routing is True

    def test_wireguard_status_invalid_interface(self):
        with pytest.raises(pydantic.ValidationError):
            WireGuardStatusInput(interface="bad-name")

    def test_network_listeners_port_too_low(self):
        with pytest.raises(pydantic.ValidationError):
            NetworkListenersInput(port=0)

    def test_network_listeners_port_too_high(self):
        with pytest.raises(pydantic.ValidationError):
            NetworkListenersInput(port=70000)

    def test_network_listeners_valid_port(self):
        inp = NetworkListenersInput(port=8080, protocol="tcp", include_loopback=True)
        assert inp.port == 8080
        assert inp.protocol == "tcp"
        assert inp.include_loopback is True

    def test_wireguard_rotate_keys_grace_period_too_low(self):
        with pytest.raises(pydantic.ValidationError):
            WireGuardRotateKeysInput(grace_period_seconds=-1)

    def test_wireguard_rotate_keys_grace_period_too_high(self):
        with pytest.raises(pydantic.ValidationError):
            WireGuardRotateKeysInput(grace_period_seconds=7200)


# =============================================================================
# WireGuardRestartCapability tests
# =============================================================================


class TestWireGuardRestartCapability:
    """Tests for wireguard.restart capability."""

    def setup_method(self):
        self.cap = WireGuardRestartCapability()

    def test_spec_properties(self):
        spec = self.cap.spec
        assert spec.name == "wireguard.restart"
        assert spec.domain == "network"
        assert spec.risk == "medium"
        assert spec.requires_privilege is True
        assert spec.idempotent is True

    @patch(f"{_NET}._is_wireguard_available", return_value=False)
    def test_dry_run_wg_unavailable(self, mock_avail):
        inp = WireGuardRestartInput()
        result = self.cap.dry_run(inp)
        assert result.is_valid is False
        assert "wg-quick" in result.validation_errors[0]

    @patch(f"{_NET}._interface_exists", return_value=True)
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_dry_run_config_missing(self, mock_avail, mock_iface):
        inp = WireGuardRestartInput(interface="wg0")
        with patch.object(Path, "exists", return_value=False):
            result = self.cap.dry_run(inp)
        assert result.is_valid is False
        assert "Config not found" in result.validation_errors[0]

    @patch(f"{_NET}._interface_exists", return_value=True)
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_dry_run_valid_interface_up(self, mock_avail, mock_iface):
        inp = WireGuardRestartInput(interface="wg0")
        with patch.object(Path, "exists", return_value=True):
            result = self.cap.dry_run(inp)
        assert result.is_valid is True
        assert result.requires_confirmation is True
        assert "currently up" in result.preview_text
        assert "wg-quick down" in str(result.would_execute)
        assert "wg-quick up" in str(result.would_execute)

    @patch(f"{_NET}._interface_exists", return_value=False)
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_dry_run_valid_interface_down(self, mock_avail, mock_iface):
        inp = WireGuardRestartInput(interface="wg0")
        with patch.object(Path, "exists", return_value=True):
            result = self.cap.dry_run(inp)
        assert result.is_valid is True
        assert "currently down" in result.preview_text

    @patch(f"{_NET}._is_wireguard_available", return_value=False)
    def test_run_wg_unavailable(self, mock_avail):
        inp = WireGuardRestartInput()
        result = self.cap.run(inp)
        assert result.success is False
        assert "wg-quick is not available" in result.error

    @patch(f"{_NET}.time.sleep")
    @patch(f"{_NET}._interface_exists")
    @patch(f"{_NET}._run_privileged_command")
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_run_success_was_up_no_handshake_verify(self, mock_avail, mock_cmd, mock_iface, mock_sleep):
        """Restart succeeds when interface was up; skip handshake verification."""
        mock_iface.side_effect = [True, True]  # was_up=True, now_up=True
        mock_cmd.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        inp = WireGuardRestartInput(verify_handshake=False)
        result = self.cap.run(inp)
        assert result.success is True
        assert result.output.was_up is True
        assert result.output.now_up is True
        assert result.output.handshake_verified is False
        # Should have a warning about unverified handshake
        assert len(result.warnings) == 1

    @patch(f"{_NET}.time.sleep")
    @patch(f"{_NET}._interface_exists")
    @patch(f"{_NET}._run_privileged_command")
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_run_was_down_brings_up(self, mock_avail, mock_cmd, mock_iface, mock_sleep):
        """When interface was down, only wg-quick up is called."""
        mock_iface.side_effect = [False, True]  # was_up=False, now_up=True
        mock_cmd.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        inp = WireGuardRestartInput(verify_handshake=False)
        result = self.cap.run(inp)
        assert result.success is True
        assert result.output.was_up is False
        assert result.output.now_up is True
        # Only wg-quick up should be in commands (not down)
        assert len(result.evidence.commands_executed) == 1
        assert "up" in result.evidence.commands_executed[0]

    @patch(f"{_NET}.time.sleep")
    @patch(f"{_NET}._interface_exists")
    @patch(f"{_NET}._run_privileged_command")
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_run_wg_quick_up_fails(self, mock_avail, mock_cmd, mock_iface, mock_sleep):
        """When wg-quick up fails, result is failure."""
        mock_iface.return_value = False  # was_up=False
        mock_cmd.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="failed to up")
        inp = WireGuardRestartInput(verify_handshake=False)
        result = self.cap.run(inp)
        assert result.success is False
        assert "wg-quick up failed" in result.error

    @patch(f"{_NET}.time.time")
    @patch(f"{_NET}.time.sleep")
    @patch(f"{_NET}._interface_exists")
    @patch(f"{_NET}._run_privileged_command")
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_run_handshake_verified(self, mock_avail, mock_cmd, mock_iface, mock_sleep, mock_time):
        """Handshake verification succeeds when peer has recent handshake."""
        mock_iface.side_effect = [False, True]  # was_up=False, now_up=True
        wg_show_output = "interface: wg0\npeer: AAAA\n  latest handshake: 30 seconds ago\n"
        mock_cmd.side_effect = [
            # wg-quick up
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            # wg show
            subprocess.CompletedProcess(args=[], returncode=0, stdout=wg_show_output, stderr=""),
        ]
        # start_time=100, first time.time() in while loop check passes, then finish
        mock_time.side_effect = [100, 100, 101, 150]
        inp = WireGuardRestartInput(verify_handshake=True, handshake_timeout_sec=60)
        result = self.cap.run(inp)
        assert result.success is True
        assert result.output.handshake_verified is True
        assert result.output.handshake_age_sec == 30
        # No warning since handshake was verified
        assert result.warnings == ()

    @patch(f"{_NET}._interface_exists")
    @patch(f"{_NET}._run_privileged_command")
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_run_timeout_exception(self, mock_avail, mock_cmd, mock_iface):
        """subprocess.TimeoutExpired is caught and reported."""
        mock_iface.return_value = True  # was_up=True
        mock_cmd.side_effect = subprocess.TimeoutExpired("wg-quick", 30)
        inp = WireGuardRestartInput(verify_handshake=False)
        result = self.cap.run(inp)
        assert result.success is False
        assert "timed out" in result.error

    @patch(f"{_NET}._interface_exists")
    @patch(f"{_NET}._run_privileged_command")
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_run_generic_exception(self, mock_avail, mock_cmd, mock_iface):
        """Generic exceptions are caught and reported."""
        mock_iface.return_value = False
        mock_cmd.side_effect = RuntimeError("boom")
        inp = WireGuardRestartInput(verify_handshake=False)
        result = self.cap.run(inp)
        assert result.success is False
        assert "boom" in result.error

    def test_parse_handshake_age_hours_minutes_seconds(self):
        age = self.cap._parse_handshake_age("1 hour, 2 minutes, 30 seconds ago")
        assert age == 3600 + 120 + 30

    def test_parse_handshake_age_seconds_only(self):
        age = self.cap._parse_handshake_age("45 seconds ago")
        assert age == 45

    def test_parse_handshake_age_minutes_only(self):
        age = self.cap._parse_handshake_age("2 minutes ago")
        assert age == 120

    def test_parse_handshake_age_hours_only(self):
        age = self.cap._parse_handshake_age("2 hours ago")
        assert age == 7200

    def test_parse_handshake_age_none_string(self):
        assert self.cap._parse_handshake_age("(none)") is None

    def test_parse_handshake_age_empty_string(self):
        assert self.cap._parse_handshake_age("") is None

    def test_parse_handshake_age_unparseable(self):
        assert self.cap._parse_handshake_age("unknown format") is None

    @patch(f"{_NET}._interface_exists", return_value=True)
    def test_verify_interface_up(self, mock_iface):
        inp = WireGuardRestartInput()
        result = self.cap.verify(inp)
        assert result.passed is True
        assert result.actual_state == {"is_up": True}
        assert result.discrepancies == ()

    @patch(f"{_NET}._interface_exists", return_value=False)
    def test_verify_interface_down(self, mock_iface):
        inp = WireGuardRestartInput()
        result = self.cap.verify(inp)
        assert result.passed is False
        assert len(result.discrepancies) == 1
        assert "not up" in result.discrepancies[0]

    def test_rollback_no_output(self):
        """Rollback fails when there is no output from the original run."""
        inp = WireGuardRestartInput()
        cap_result = MagicMock()
        cap_result.output = None
        result = self.cap.rollback(inp, cap_result)
        assert result.success is False
        assert "No output" in result.error

    @patch(f"{_NET}._run_privileged_command")
    def test_rollback_was_down_brings_down(self, mock_cmd):
        """If interface was down before restart, rollback brings it down."""
        mock_cmd.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        inp = WireGuardRestartInput(interface="wg0")
        cap_result = MagicMock()
        cap_result.output.was_up = False
        result = self.cap.rollback(inp, cap_result)
        assert result.success is True
        assert "wg0" in result.rolled_back

    @patch(f"{_NET}._run_privileged_command", side_effect=RuntimeError("fail"))
    def test_rollback_was_down_exception(self, mock_cmd):
        """Rollback failure when bringing interface down raises."""
        inp = WireGuardRestartInput(interface="wg0")
        cap_result = MagicMock()
        cap_result.output.was_up = False
        result = self.cap.rollback(inp, cap_result)
        assert result.success is False
        assert "fail" in result.error

    def test_rollback_was_up_nothing_to_do(self):
        """If interface was already up, rollback is a no-op success."""
        inp = WireGuardRestartInput(interface="wg0")
        cap_result = MagicMock()
        cap_result.output.was_up = True
        result = self.cap.rollback(inp, cap_result)
        assert result.success is True
        assert result.rolled_back == ()


# =============================================================================
# WireGuardStatusCapability tests
# =============================================================================


class TestWireGuardStatusCapability:
    """Tests for wireguard.status capability."""

    def setup_method(self):
        self.cap = WireGuardStatusCapability()

    def test_spec_properties(self):
        spec = self.cap.spec
        assert spec.name == "wireguard.status"
        assert spec.risk == "none"
        assert spec.side_effects == ()

    @patch(f"{_NET}._is_wireguard_available", return_value=False)
    def test_dry_run_wg_unavailable(self, mock_avail):
        inp = WireGuardStatusInput()
        result = self.cap.dry_run(inp)
        assert result.is_valid is False

    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_dry_run_valid(self, mock_avail):
        inp = WireGuardStatusInput(interface="wg0")
        result = self.cap.dry_run(inp)
        assert result.is_valid is True
        assert result.requires_confirmation is False
        assert "wg0" in result.preview_text

    @patch(f"{_NET}._is_wireguard_available", return_value=False)
    def test_run_wg_unavailable(self, mock_avail):
        inp = WireGuardStatusInput()
        result = self.cap.run(inp)
        assert result.success is False
        assert "wg-quick is not available" in result.error

    @patch(f"{_NET}._interface_exists", return_value=False)
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_run_interface_down(self, mock_avail, mock_iface):
        inp = WireGuardStatusInput()
        result = self.cap.run(inp)
        assert result.success is True
        assert result.output.is_up is False
        assert result.output.peers == ()

    @patch(f"{_NET}._run_privileged_command")
    @patch(f"{_NET}._interface_exists", return_value=True)
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_run_wg_show_fails(self, mock_avail, mock_iface, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="permission denied"
        )
        inp = WireGuardStatusInput()
        result = self.cap.run(inp)
        assert result.success is False
        assert "wg show failed" in result.error

    @patch(f"{_NET}._run_command")
    @patch(f"{_NET}._run_privileged_command")
    @patch(f"{_NET}._interface_exists", return_value=True)
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_run_success_with_peers_and_routes(self, mock_avail, mock_iface, mock_priv_cmd, mock_cmd):
        """Full status check with peers and routing."""
        wg_output = (
            "interface: wg0\n"
            "  public key: PUBKEY123\n"
            "  listening port: 51820\n"
            "peer: PEERPUBKEY01234567890\n"
            "  endpoint: 10.0.0.1:51820\n"
            "  allowed ips: 10.0.0.0/24\n"
            "  latest handshake: 45 seconds ago\n"
            "  transfer: 100MiB received, 50MiB sent\n"
        )
        route_output = "10.0.0.0/24 dev wg0 scope link"
        mock_priv_cmd.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=wg_output, stderr="")
        mock_cmd.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=route_output, stderr="")
        inp = WireGuardStatusInput(check_routing=True)
        result = self.cap.run(inp)
        assert result.success is True
        assert result.output.is_up is True
        assert result.output.public_key == "PUBKEY123"
        assert result.output.listening_port == 51820
        assert result.output.has_routes is True
        assert len(result.output.peers) == 1
        peer = result.output.peers[0]
        assert peer.endpoint == "10.0.0.1:51820"
        assert peer.is_stale is False
        assert result.output.any_stale_handshakes is False

    @patch(f"{_NET}._run_command")
    @patch(f"{_NET}._run_privileged_command")
    @patch(f"{_NET}._interface_exists", return_value=True)
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_run_stale_handshake_detected(self, mock_avail, mock_iface, mock_priv_cmd, mock_cmd):
        """Peer with no handshake is detected as stale."""
        wg_output = (
            "interface: wg0\npeer: STALEP01234567890123\n  latest handshake: (none)\n  transfer: 0B received, 0B sent\n"
        )
        mock_priv_cmd.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=wg_output, stderr="")
        mock_cmd.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        inp = WireGuardStatusInput(check_routing=True)
        result = self.cap.run(inp)
        assert result.success is True
        assert result.output.any_stale_handshakes is True
        assert result.output.peers[0].is_stale is True

    @patch(f"{_NET}._run_privileged_command")
    @patch(f"{_NET}._interface_exists", return_value=True)
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_run_no_routing_check(self, mock_avail, mock_iface, mock_cmd):
        """When check_routing=False, no route check is performed."""
        wg_output = "interface: wg0\n"
        mock_cmd.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=wg_output, stderr="")
        inp = WireGuardStatusInput(check_routing=False)
        result = self.cap.run(inp)
        assert result.success is True
        assert result.output.has_routes is False
        # Only one call (wg show), no ip route call
        mock_cmd.assert_called_once()

    @patch(f"{_NET}._interface_exists", return_value=True)
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    @patch(f"{_NET}._run_privileged_command", side_effect=RuntimeError("unexpected"))
    def test_run_generic_exception(self, mock_cmd, mock_avail, mock_iface):
        inp = WireGuardStatusInput()
        result = self.cap.run(inp)
        assert result.success is False
        assert "unexpected" in result.error

    def test_parse_handshake_age_none_value(self):
        assert self.cap._parse_handshake_age("(none)") is None
        assert self.cap._parse_handshake_age("") is None

    def test_parse_handshake_age_minutes(self):
        age = self.cap._parse_handshake_age("3 minutes, 10 seconds ago")
        assert age == 190


# =============================================================================
# NetworkListenersCapability tests
# =============================================================================


class TestNetworkListenersCapability:
    """Tests for network.listeners capability."""

    def setup_method(self):
        self.cap = NetworkListenersCapability()

    def test_spec_properties(self):
        spec = self.cap.spec
        assert spec.name == "network.listeners"
        assert spec.risk == "none"
        assert spec.requires_privilege is False

    def test_dry_run_always_valid(self):
        inp = NetworkListenersInput()
        result = self.cap.dry_run(inp)
        assert result.is_valid is True
        assert result.requires_confirmation is False

    @patch.object(Path, "exists", return_value=False)
    def test_run_no_proc_files(self, mock_exists):
        """When /proc/net files do not exist, return empty list."""
        inp = NetworkListenersInput()
        result = self.cap.run(inp)
        assert result.success is True
        assert result.output.total == 0
        assert result.output.listeners == ()

    @patch.object(NetworkListenersCapability, "_scan_listeners", side_effect=RuntimeError("scan failed"))
    def test_run_exception_handling(self, mock_scan):
        inp = NetworkListenersInput()
        result = self.cap.run(inp)
        assert result.success is False
        assert "scan failed" in result.error

    def test_hex_to_ip_ipv4(self):
        # 0100007F in little-endian = 127.0.0.1
        result = self.cap._hex_to_ip("0100007F")
        assert result == "127.0.0.1"

    def test_hex_to_ip_ipv4_wildcard(self):
        result = self.cap._hex_to_ip("00000000")
        assert result == "0.0.0.0"

    def test_hex_to_ip_ipv6_any(self):
        result = self.cap._hex_to_ip("00000000000000000000000000000000")
        assert result == "::"

    def test_hex_to_ip_ipv6_loopback(self):
        result = self.cap._hex_to_ip("00000000000000000000000001000000")
        assert result == "::1"

    def test_hex_to_ip_ipv6_other(self):
        """Non-standard IPv6 returns the raw hex."""
        result = self.cap._hex_to_ip("AABBCCDD11223344AABBCCDD11223344")
        assert result == "AABBCCDD11223344AABBCCDD11223344"

    def test_get_process_for_inode_zero(self):
        pid, name = self.cap._get_process_for_inode("0")
        assert pid is None
        assert name == "kernel"

    @patch.object(Path, "iterdir", side_effect=OSError("cannot read"))
    def test_get_process_for_inode_proc_error(self, mock_iterdir):
        """OSError scanning /proc is caught; returns unknown."""
        pid, name = self.cap._get_process_for_inode("99999")
        assert pid is None
        assert name == "unknown"

    def test_scan_listeners_tcp_listen_entry(self):
        """Scan parses a TCP LISTEN entry from synthetic /proc/net/tcp data."""
        # /proc/net/tcp format: sl local_address rem_address st ...
        # 00000000:1F90 = 0.0.0.0:8080  state 0A = LISTEN
        tcp_content = (
            "  sl  local_address rem_address   st tx_queue rx_queue "
            "tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 00000000:1F90 00000000:0000 0A 00000000:00000000 "
            "00:00000000 00000000     0        0 12345 1 0000000000000000 100 0 0 10 0\n"
        )

        orig_exists = Path.exists
        orig_read_text = Path.read_text

        def mock_exists(self_path):
            if "/proc/net/" in str(self_path):
                return str(self_path) == "/proc/net/tcp"
            return orig_exists(self_path)

        def mock_read_text(self_path, *args, **kwargs):
            if str(self_path) == "/proc/net/tcp":
                return tcp_content
            return orig_read_text(self_path, *args, **kwargs)

        with (
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "read_text", mock_read_text),
            patch.object(self.cap, "_get_process_for_inode", return_value=(1234, "nginx")),
        ):
            inp = NetworkListenersInput(protocol="tcp", include_loopback=True)
            listeners = self.cap._scan_listeners(inp)

        assert len(listeners) == 1
        assert listeners[0].port == 8080
        assert listeners[0].protocol == "tcp"
        assert listeners[0].address == "0.0.0.0"
        assert listeners[0].is_wildcard is True
        assert listeners[0].pid == 1234
        assert listeners[0].process_name == "nginx"

    def test_scan_listeners_filters_non_listen_tcp(self):
        """Non-LISTEN TCP entries (state != 0A) are skipped."""
        # state 01 = ESTABLISHED
        tcp_content = (
            "  sl  local_address rem_address   st tx_queue rx_queue "
            "tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 00000000:1F90 0A0A0A01:0050 01 00000000:00000000 "
            "00:00000000 00000000     0        0 12345 1 0000000000000000 100 0 0 10 0\n"
        )

        orig_exists = Path.exists

        def mock_exists(self_path):
            if "/proc/net/" in str(self_path):
                return str(self_path) == "/proc/net/tcp"
            return orig_exists(self_path)

        with patch.object(Path, "exists", mock_exists), patch.object(Path, "read_text", return_value=tcp_content):
            inp = NetworkListenersInput(protocol="tcp")
            listeners = self.cap._scan_listeners(inp)

        assert len(listeners) == 0

    def test_scan_listeners_skips_loopback_by_default(self):
        """Loopback listeners are skipped when include_loopback=False."""
        # 0100007F:0050 = 127.0.0.1:80  state 0A = LISTEN
        tcp_content = (
            "  sl  local_address rem_address   st tx_queue rx_queue "
            "tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 0100007F:0050 00000000:0000 0A 00000000:00000000 "
            "00:00000000 00000000     0        0 12345 1 0000000000000000 100 0 0 10 0\n"
        )

        orig_exists = Path.exists

        def mock_exists(self_path):
            if "/proc/net/" in str(self_path):
                return str(self_path) == "/proc/net/tcp"
            return orig_exists(self_path)

        with (
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "read_text", return_value=tcp_content),
            patch.object(self.cap, "_get_process_for_inode", return_value=(None, "unknown")),
        ):
            inp = NetworkListenersInput(protocol="tcp", include_loopback=False)
            listeners = self.cap._scan_listeners(inp)

        assert len(listeners) == 0

    def test_scan_listeners_port_filter(self):
        """Only listeners on the specified port are returned."""
        tcp_content = (
            "  sl  local_address rem_address   st tx_queue rx_queue "
            "tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 00000000:0050 00000000:0000 0A 00000000:00000000 "
            "00:00000000 00000000     0        0 111 1 0000000000000000 100 0 0 10 0\n"
            "   1: 00000000:01BB 00000000:0000 0A 00000000:00000000 "
            "00:00000000 00000000     0        0 222 1 0000000000000000 100 0 0 10 0\n"
        )

        orig_exists = Path.exists

        def mock_exists(self_path):
            if "/proc/net/" in str(self_path):
                return str(self_path) == "/proc/net/tcp"
            return orig_exists(self_path)

        with (
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "read_text", return_value=tcp_content),
            patch.object(self.cap, "_get_process_for_inode", return_value=(None, "unknown")),
        ):
            # Port 0x0050 = 80, 0x01BB = 443 -- filter for port 443
            inp = NetworkListenersInput(protocol="tcp", port=443, include_loopback=True)
            listeners = self.cap._scan_listeners(inp)

        assert len(listeners) == 1
        assert listeners[0].port == 443

    def test_scan_listeners_deduplicates(self):
        """Duplicate entries by port/protocol/address are deduplicated."""
        # Two identical entries
        tcp_content = (
            "  sl  local_address rem_address   st tx_queue rx_queue "
            "tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 00000000:0050 00000000:0000 0A 00000000:00000000 "
            "00:00000000 00000000     0        0 111 1 0000000000000000 100 0 0 10 0\n"
            "   1: 00000000:0050 00000000:0000 0A 00000000:00000000 "
            "00:00000000 00000000     0        0 111 1 0000000000000000 100 0 0 10 0\n"
        )

        orig_exists = Path.exists

        def mock_exists(self_path):
            if "/proc/net/" in str(self_path):
                return str(self_path) == "/proc/net/tcp"
            return orig_exists(self_path)

        with (
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "read_text", return_value=tcp_content),
            patch.object(self.cap, "_get_process_for_inode", return_value=(None, "unknown")),
        ):
            inp = NetworkListenersInput(protocol="tcp", include_loopback=True)
            listeners = self.cap._scan_listeners(inp)

        assert len(listeners) == 1

    def test_scan_listeners_read_error_is_caught(self):
        """Exceptions reading /proc/net files are caught and logged."""
        orig_exists = Path.exists

        def mock_exists(self_path):
            if "/proc/net/" in str(self_path):
                return str(self_path) == "/proc/net/tcp"
            return orig_exists(self_path)

        with (
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "read_text", side_effect=PermissionError("denied")),
        ):
            inp = NetworkListenersInput(protocol="tcp")
            listeners = self.cap._scan_listeners(inp)

        assert len(listeners) == 0

    def test_scan_listeners_short_line_skipped(self):
        """Lines with fewer than 10 columns are skipped."""
        tcp_content = (
            "  sl  local_address rem_address   st tx_queue rx_queue "
            "tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 00000000:0050 00000000:0000\n"
        )

        orig_exists = Path.exists

        def mock_exists(self_path):
            if "/proc/net/" in str(self_path):
                return str(self_path) == "/proc/net/tcp"
            return orig_exists(self_path)

        with patch.object(Path, "exists", mock_exists), patch.object(Path, "read_text", return_value=tcp_content):
            inp = NetworkListenersInput(protocol="tcp")
            listeners = self.cap._scan_listeners(inp)

        assert len(listeners) == 0

    def test_scan_listeners_udp_protocol(self):
        """UDP listeners are found (all UDP entries are treated as listening)."""
        # UDP doesn't filter by state 0A
        udp_content = (
            "  sl  local_address rem_address   st tx_queue rx_queue "
            "tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 00000000:14E9 00000000:0000 07 00000000:00000000 "
            "00:00000000 00000000     0        0 333 1 0000000000000000 100 0 0 10 0\n"
        )

        orig_exists = Path.exists

        def mock_exists(self_path):
            if "/proc/net/" in str(self_path):
                return str(self_path) == "/proc/net/udp"
            return orig_exists(self_path)

        with (
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "read_text", return_value=udp_content),
            patch.object(self.cap, "_get_process_for_inode", return_value=(None, "unknown")),
        ):
            inp = NetworkListenersInput(protocol="udp", include_loopback=True)
            listeners = self.cap._scan_listeners(inp)

        # UDP does not filter by state, so this entry should appear
        assert len(listeners) == 1
        assert listeners[0].protocol == "udp"
        assert listeners[0].port == 0x14E9


# =============================================================================
# WireGuardGenerateKeyCapability tests
# =============================================================================


class TestWireGuardGenerateKeyCapability:
    """Tests for wireguard.generate-key capability."""

    def setup_method(self):
        self.cap = WireGuardGenerateKeyCapability()

    def test_spec_properties(self):
        spec = self.cap.spec
        assert spec.name == "wireguard.generate-key"
        assert spec.risk == "low"
        assert spec.idempotent is False

    @patch(f"{_NET}._is_wireguard_available", return_value=False)
    def test_dry_run_wg_unavailable(self, mock_avail):
        inp = WireGuardGenerateKeyInput()
        result = self.cap.dry_run(inp)
        assert result.is_valid is False

    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_dry_run_valid(self, mock_avail):
        inp = WireGuardGenerateKeyInput()
        result = self.cap.dry_run(inp)
        assert result.is_valid is True
        assert "wg genkey" in result.would_execute

    @patch("elle.ops.wireguard.keys.generate_keypair", return_value=("PRIVKEY", "PUBKEY"))
    def test_run_generates_keypair(self, mock_keygen):
        inp = WireGuardGenerateKeyInput(include_preshared_key=False)
        result = self.cap.run(inp)
        assert result.success is True
        assert result.output.private_key == "PRIVKEY"
        assert result.output.public_key == "PUBKEY"
        assert result.output.preshared_key is None

    @patch("elle.ops.wireguard.keys.generate_preshared_key", return_value="PSKKEY")
    @patch("elle.ops.wireguard.keys.generate_keypair", return_value=("PRIVKEY", "PUBKEY"))
    def test_run_with_preshared_key(self, mock_keygen, mock_psk):
        inp = WireGuardGenerateKeyInput(include_preshared_key=True)
        result = self.cap.run(inp)
        assert result.success is True
        assert result.output.preshared_key == "PSKKEY"

    @patch("elle.ops.wireguard.keys.generate_keypair", side_effect=RuntimeError("wg not found"))
    def test_run_exception(self, mock_keygen):
        inp = WireGuardGenerateKeyInput()
        result = self.cap.run(inp)
        assert result.success is False
        assert "wg not found" in result.error


# =============================================================================
# WireGuardRotateKeysCapability tests
# =============================================================================


class TestWireGuardRotateKeysCapability:
    """Tests for wireguard.rotate-keys capability."""

    def setup_method(self):
        self.cap = WireGuardRotateKeysCapability()

    def test_spec_properties(self):
        spec = self.cap.spec
        assert spec.name == "wireguard.rotate-keys"
        assert spec.risk == "high"
        assert spec.requires_privilege is True
        assert len(spec.side_effects) == 2

    @patch(f"{_NET}._is_wireguard_available", return_value=False)
    def test_dry_run_wg_unavailable(self, mock_avail):
        inp = WireGuardRotateKeysInput()
        result = self.cap.dry_run(inp)
        assert result.is_valid is False

    @patch(f"{_NET}._interface_exists", return_value=True)
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_dry_run_config_missing(self, mock_avail, mock_iface):
        inp = WireGuardRotateKeysInput()
        with patch.object(Path, "exists", return_value=False):
            result = self.cap.dry_run(inp)
        assert result.is_valid is False
        assert "Config not found" in result.validation_errors[0]

    @patch(f"{_NET}._interface_exists", return_value=False)
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_dry_run_valid_interface_down(self, mock_avail, mock_iface):
        inp = WireGuardRotateKeysInput(interface="wg0")
        with patch.object(Path, "exists", return_value=True):
            result = self.cap.dry_run(inp)
        assert result.is_valid is True
        assert result.requires_confirmation is True
        assert "currently down" in result.preview_text

    @patch(f"{_NET}._interface_exists", return_value=True)
    @patch(f"{_NET}._is_wireguard_available", return_value=True)
    def test_dry_run_valid_interface_up(self, mock_avail, mock_iface):
        inp = WireGuardRotateKeysInput(interface="wg0", grace_period_seconds=60)
        with patch.object(Path, "exists", return_value=True):
            result = self.cap.dry_run(inp)
        assert result.is_valid is True
        assert "currently up" in result.preview_text
        assert "60s" in result.preview_text

    @patch("elle.ops.wireguard.keys.rotate_keys_safely")
    def test_run_success(self, mock_rotate):
        mock_rotate.return_value = SimpleNamespace(
            success=True,
            interface="wg0",
            old_public_key="OLDPUBKEY1234567890",
            new_public_key="NEWPUBKEY1234567890",
            backup_path="/etc/wireguard/wg0.conf.bak",
            steps_completed=("genkey", "pubkey", "backup", "update", "restart"),
            error=None,
        )
        inp = WireGuardRotateKeysInput()
        result = self.cap.run(inp)
        assert result.success is True
        assert result.output.old_public_key == "OLDPUBKEY1234567890"
        assert result.output.new_public_key == "NEWPUBKEY1234567890"
        assert result.output.backup_path == "/etc/wireguard/wg0.conf.bak"
        assert len(result.warnings) == 1
        assert "NEWPUBKEY1234567890" in result.warnings[0]

    @patch("elle.ops.wireguard.keys.rotate_keys_safely")
    def test_run_rotation_failure(self, mock_rotate):
        mock_rotate.return_value = SimpleNamespace(
            success=False,
            interface="wg0",
            old_public_key=None,
            new_public_key=None,
            backup_path=None,
            steps_completed=("genkey",),
            error="backup failed",
        )
        inp = WireGuardRotateKeysInput()
        result = self.cap.run(inp)
        assert result.success is False
        assert result.error == "backup failed"

    @patch("elle.ops.wireguard.keys.rotate_keys_safely", side_effect=ImportError("no keys module"))
    def test_run_import_error(self, mock_rotate):
        inp = WireGuardRotateKeysInput()
        result = self.cap.run(inp)
        assert result.success is False
        assert "no keys module" in result.error

    def test_rollback_no_output(self):
        inp = WireGuardRotateKeysInput()
        cap_result = MagicMock()
        cap_result.output = None
        result = self.cap.rollback(inp, cap_result)
        assert result.success is False
        assert "No backup path" in result.error

    def test_rollback_no_backup_path(self):
        inp = WireGuardRotateKeysInput()
        cap_result = MagicMock()
        cap_result.output.backup_path = None
        result = self.cap.rollback(inp, cap_result)
        assert result.success is False
        assert "No backup path" in result.error

    @patch(f"{_NET}._run_privileged_command")
    @patch("shutil.copy2")
    def test_rollback_success(self, mock_copy, mock_cmd):
        """Rollback restores backup and restarts interface."""
        mock_cmd.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        inp = WireGuardRotateKeysInput(interface="wg0")
        cap_result = MagicMock()
        cap_result.output.backup_path = "/tmp/wg0.conf.bak"

        with patch.object(Path, "exists", return_value=True):
            result = self.cap.rollback(inp, cap_result)

        assert result.success is True
        assert "/etc/wireguard/wg0.conf" in result.rolled_back
        mock_copy.assert_called_once()

    @patch("shutil.copy2", side_effect=PermissionError("denied"))
    def test_rollback_copy_failure(self, mock_copy):
        inp = WireGuardRotateKeysInput(interface="wg0")
        cap_result = MagicMock()
        cap_result.output.backup_path = "/tmp/wg0.conf.bak"

        with patch.object(Path, "exists", return_value=True):
            result = self.cap.rollback(inp, cap_result)

        assert result.success is False
        assert "denied" in result.error

    def test_rollback_backup_file_missing(self):
        inp = WireGuardRotateKeysInput(interface="wg0")
        cap_result = MagicMock()
        cap_result.output.backup_path = "/tmp/nonexistent.bak"

        with patch.object(Path, "exists", return_value=False):
            result = self.cap.rollback(inp, cap_result)

        assert result.success is False
        assert "Backup not found" in result.error


# =============================================================================
# NetworkDiagnoseCapability tests
# =============================================================================


class TestNetworkDiagnoseCapability:
    """Tests for network.diagnose capability."""

    def setup_method(self):
        self.cap = NetworkDiagnoseCapability()

    def test_spec_properties(self):
        spec = self.cap.spec
        assert spec.name == "network.diagnose"
        assert spec.risk == "none"
        assert spec.side_effects == ()

    def test_dry_run_with_port(self):
        inp = NetworkDiagnoseInput(target="example.com", port=443)
        result = self.cap.dry_run(inp)
        assert result.is_valid is True
        assert any("443" in cmd for cmd in result.would_execute)
        assert "example.com:443" in result.preview_text

    def test_dry_run_without_port(self):
        inp = NetworkDiagnoseInput(target="example.com")
        result = self.cap.dry_run(inp)
        assert result.is_valid is True
        assert any("ping" in cmd.lower() for cmd in result.would_execute)

    @patch("elle.cli.network.diagnosis.diagnose_connectivity")
    def test_run_reachable(self, mock_diagnose):
        mock_diagnose.return_value = SimpleNamespace(
            target="example.com",
            port=443,
            reachable=True,
            likely_cause=None,
            summary="Target is reachable on port 443",
            checks=[
                SimpleNamespace(passed=True),
                SimpleNamespace(passed=True),
                SimpleNamespace(passed=True),
            ],
            suggestions=(),
        )
        inp = NetworkDiagnoseInput(target="example.com", port=443)
        result = self.cap.run(inp)
        assert result.success is True
        assert result.output.reachable is True
        assert result.output.checks_passed == 3
        assert result.output.checks_failed == 0

    @patch("elle.cli.network.diagnosis.diagnose_connectivity")
    def test_run_unreachable(self, mock_diagnose):
        mock_diagnose.return_value = SimpleNamespace(
            target="10.0.0.99",
            port=22,
            reachable=False,
            likely_cause="No route to host",
            summary="Cannot reach 10.0.0.99:22",
            checks=[
                SimpleNamespace(passed=True),
                SimpleNamespace(passed=False),
            ],
            suggestions=("Check routing table", "Verify firewall rules"),
        )
        inp = NetworkDiagnoseInput(target="10.0.0.99", port=22)
        result = self.cap.run(inp)
        assert result.success is True
        assert result.output.reachable is False
        assert result.output.likely_cause == "No route to host"
        assert result.output.checks_passed == 1
        assert result.output.checks_failed == 1
        assert len(result.output.suggestions) == 2

    @patch("elle.cli.network.diagnosis.diagnose_connectivity", side_effect=ImportError("missing module"))
    def test_run_exception(self, mock_diagnose):
        inp = NetworkDiagnoseInput(target="example.com")
        result = self.cap.run(inp)
        assert result.success is False
        assert "Network diagnosis failed" in result.error


# =============================================================================
# NETWORK_CAPABILITIES export test
# =============================================================================


class TestNetworkCapabilitiesExport:
    """Tests for the module-level NETWORK_CAPABILITIES list."""

    def test_all_capabilities_exported(self):
        assert WireGuardRestartCapability in NETWORK_CAPABILITIES
        assert WireGuardStatusCapability in NETWORK_CAPABILITIES
        assert NetworkListenersCapability in NETWORK_CAPABILITIES
        assert WireGuardGenerateKeyCapability in NETWORK_CAPABILITIES
        assert WireGuardRotateKeysCapability in NETWORK_CAPABILITIES
        assert NetworkDiagnoseCapability in NETWORK_CAPABILITIES

    def test_capability_count(self):
        assert len(NETWORK_CAPABILITIES) == 6

    def test_all_spec_names(self):
        names = {cap().spec.name for cap in NETWORK_CAPABILITIES}
        expected = {
            "wireguard.restart",
            "wireguard.status",
            "wireguard.generate-key",
            "wireguard.rotate-keys",
            "network.listeners",
            "network.diagnose",
        }
        assert names == expected
