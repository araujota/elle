"""Tests for Network capabilities."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from elle.capabilities.core.network import (
    NETWORK_CAPABILITIES,
    NetworkListenersCapability,
    NetworkListenersInput,
    WireGuardRestartCapability,
    WireGuardRestartInput,
    WireGuardStatusCapability,
    WireGuardStatusInput,
)


class TestWireGuardRestartCapability:
    """Tests for WireGuardRestartCapability."""

    def test_spec(self):
        """Test capability spec is valid."""
        cap = WireGuardRestartCapability()
        spec = cap.spec

        assert spec.name == "wireguard.restart"
        assert spec.domain == "network"
        assert spec.risk == "medium"
        assert spec.requires_privilege is True

    @patch("elle.capabilities.core.network._is_wireguard_available")
    def test_dry_run_wg_not_available(self, mock_available):
        """Test dry run when WireGuard is not available."""
        mock_available.return_value = False

        cap = WireGuardRestartCapability()
        input_data = WireGuardRestartInput(interface="wg0")

        result = cap.dry_run(input_data)

        assert result.is_valid is False
        assert "not found" in result.preview_text.lower() or "not available" in result.preview_text.lower()

    @patch("elle.capabilities.core.network._is_wireguard_available")
    @patch("elle.capabilities.core.network._interface_exists")
    @patch("pathlib.Path.exists")
    def test_dry_run_success(self, mock_path_exists, mock_iface_exists, mock_available):
        """Test successful dry run."""
        mock_available.return_value = True
        mock_iface_exists.return_value = True
        mock_path_exists.return_value = True

        cap = WireGuardRestartCapability()
        input_data = WireGuardRestartInput(interface="wg0")

        result = cap.dry_run(input_data)

        assert result.is_valid is True
        assert "wg-quick down" in str(result.would_execute)
        assert "wg-quick up" in str(result.would_execute)

    @patch("elle.capabilities.core.network._is_wireguard_available")
    @patch("elle.capabilities.core.network._interface_exists")
    @patch("elle.capabilities.core.network._run_command")
    def test_run_success(self, mock_run, mock_iface_exists, mock_available):
        """Test successful WireGuard restart."""
        mock_available.return_value = True
        mock_iface_exists.side_effect = [True, True]  # was_up, now_up
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        cap = WireGuardRestartCapability()
        input_data = WireGuardRestartInput(
            interface="wg0",
            verify_handshake=False,  # Skip handshake verification for test
        )

        result = cap.run(input_data)

        assert result.success is True
        assert result.output.interface == "wg0"
        assert result.output.now_up is True

    @patch("elle.capabilities.core.network._is_wireguard_available")
    def test_run_wg_not_available(self, mock_available):
        """Test run when WireGuard is not available."""
        mock_available.return_value = False

        cap = WireGuardRestartCapability()
        input_data = WireGuardRestartInput(interface="wg0")

        result = cap.run(input_data)

        assert result.success is False
        assert "not available" in result.error.lower()


class TestWireGuardStatusCapability:
    """Tests for WireGuardStatusCapability."""

    def test_spec(self):
        """Test capability spec is valid."""
        cap = WireGuardStatusCapability()
        spec = cap.spec

        assert spec.name == "wireguard.status"
        assert spec.domain == "network"
        assert spec.risk == "none"

    @patch("elle.capabilities.core.network._is_wireguard_available")
    def test_dry_run_success(self, mock_available):
        """Test successful dry run."""
        mock_available.return_value = True

        cap = WireGuardStatusCapability()
        input_data = WireGuardStatusInput(interface="wg0")

        result = cap.dry_run(input_data)

        assert result.is_valid is True
        assert "wg show" in str(result.would_execute)

    @patch("elle.capabilities.core.network._is_wireguard_available")
    @patch("elle.capabilities.core.network._interface_exists")
    def test_run_interface_down(self, mock_iface_exists, mock_available):
        """Test status when interface is down."""
        mock_available.return_value = True
        mock_iface_exists.return_value = False

        cap = WireGuardStatusCapability()
        input_data = WireGuardStatusInput(interface="wg0")

        result = cap.run(input_data)

        assert result.success is True
        assert result.output.is_up is False

    @patch("elle.capabilities.core.network._is_wireguard_available")
    @patch("elle.capabilities.core.network._interface_exists")
    @patch("elle.capabilities.core.network._run_command")
    def test_run_interface_up(self, mock_run, mock_iface_exists, mock_available):
        """Test status when interface is up."""
        mock_available.return_value = True
        mock_iface_exists.return_value = True

        # Note: transfer line must have format like "1.23 MiB received, 4.56 MiB sent"
        # The parser extracts the first two values
        wg_output = """interface: wg0
  public key: abc123xyz...
  private key: (hidden)
  listening port: 51820

peer: peer123abc...
  endpoint: 1.2.3.4:51820
  allowed ips: 10.0.0.0/24
  latest handshake: 30 seconds ago
  transfer: 1.23MiB received, 4.56MiB sent
"""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=wg_output),  # wg show
            MagicMock(returncode=0, stdout="10.0.0.0/24"),  # ip route
        ]

        cap = WireGuardStatusCapability()
        input_data = WireGuardStatusInput(interface="wg0")

        result = cap.run(input_data)

        assert result.success is True
        assert result.output.is_up is True
        assert len(result.output.peers) > 0


class TestNetworkListenersCapability:
    """Tests for NetworkListenersCapability."""

    def test_spec(self):
        """Test capability spec is valid."""
        cap = NetworkListenersCapability()
        spec = cap.spec

        assert spec.name == "network.listeners"
        assert spec.domain == "network"
        assert spec.risk == "none"

    def test_dry_run(self):
        """Test dry run returns preview."""
        cap = NetworkListenersCapability()
        input_data = NetworkListenersInput()

        result = cap.dry_run(input_data)

        assert result.is_valid is True
        assert "scan" in result.preview_text.lower()

    def test_run_returns_listeners(self):
        """Test run returns listener info."""
        cap = NetworkListenersCapability()
        input_data = NetworkListenersInput(include_loopback=True)

        # Run against actual /proc/net (will work on Linux)
        result = cap.run(input_data)

        # Should succeed even if no listeners found
        assert result.success is True
        assert result.output is not None

    def test_run_filter_by_port(self):
        """Test filtering by port."""
        cap = NetworkListenersCapability()
        input_data = NetworkListenersInput(port=22)

        result = cap.run(input_data)

        assert result.success is True
        # All listeners should be on port 22 (if any)
        for listener in result.output.listeners:
            assert listener.port == 22

    def test_run_filter_by_protocol(self):
        """Test filtering by protocol."""
        cap = NetworkListenersCapability()
        input_data = NetworkListenersInput(protocol="tcp")

        result = cap.run(input_data)

        assert result.success is True
        # All listeners should be TCP
        for listener in result.output.listeners:
            assert listener.protocol == "tcp"


class TestNetworkCapabilitiesRegistry:
    """Tests for Network capabilities registration."""

    def test_all_capabilities_exported(self):
        """Test all capabilities are in NETWORK_CAPABILITIES."""
        assert len(NETWORK_CAPABILITIES) == 5

        names = {cap().spec.name for cap in NETWORK_CAPABILITIES}
        expected = {
            "wireguard.restart",
            "wireguard.status",
            "wireguard.generate-key",
            "wireguard.rotate-keys",
            "network.listeners",
        }
        assert names == expected


class TestHandshakeAgeParsing:
    """Tests for handshake age parsing."""

    def test_parse_seconds(self):
        """Test parsing seconds."""
        cap = WireGuardRestartCapability()
        age = cap._parse_handshake_age("30 seconds ago")
        assert age == 30

    def test_parse_minutes(self):
        """Test parsing minutes."""
        cap = WireGuardRestartCapability()
        age = cap._parse_handshake_age("2 minutes ago")
        assert age == 120

    def test_parse_minutes_and_seconds(self):
        """Test parsing minutes and seconds."""
        cap = WireGuardRestartCapability()
        age = cap._parse_handshake_age("1 minute, 30 seconds ago")
        assert age == 90

    def test_parse_hours(self):
        """Test parsing hours."""
        cap = WireGuardRestartCapability()
        age = cap._parse_handshake_age("2 hours ago")
        assert age == 7200

    def test_parse_none(self):
        """Test parsing (none)."""
        cap = WireGuardRestartCapability()
        age = cap._parse_handshake_age("(none)")
        assert age is None

    def test_parse_empty(self):
        """Test parsing empty string."""
        cap = WireGuardRestartCapability()
        age = cap._parse_handshake_age("")
        assert age is None
