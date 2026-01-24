"""Tests for WireGuard domain handler and key management."""

import pytest

from elle.rag.confgen.wireguard import WireguardHandler


class TestWireguardHandler:
    """Tests for WireguardHandler."""

    def test_domain_is_wireguard(self):
        handler = WireguardHandler()
        assert handler.domain == "wireguard"

    def test_file_type_is_text(self):
        handler = WireguardHandler()
        assert handler.file_type == "text"

    def test_matches_wireguard_conf(self):
        handler = WireguardHandler()
        assert handler.matches_path("/etc/wireguard/wg0.conf")
        assert handler.matches_path("/etc/wireguard/wg1.conf")
        assert handler.matches_path("/etc/wireguard/vpn.conf")

    def test_matches_wg_prefixed_conf(self):
        handler = WireguardHandler()
        assert handler.matches_path("wg0.conf")
        assert handler.matches_path("wg-tunnel.conf")

    def test_does_not_match_other_files(self):
        handler = WireguardHandler()
        assert not handler.matches_path("/etc/netplan/config.yaml")
        assert not handler.matches_path("/etc/ssh/sshd_config")
        assert not handler.matches_path("config.conf")

    def test_validate_valid_config(self):
        handler = WireguardHandler()
        content = """
[Interface]
PrivateKey = cPvPPFPQbLZ9stQnT5J+MdG7g5pmxcpNTm7tZHsCj1s=
Address = 10.0.0.1/24
ListenPort = 51820

[Peer]
PublicKey = Jk8P+8U3HCHPFGpNt/6bL6Ge3Ke0kM8mLg3UpSi4PDs=
AllowedIPs = 10.0.0.2/32
"""
        result = handler.validate_content(content)
        assert result.valid

    def test_validate_missing_interface(self):
        handler = WireguardHandler()
        content = """
[Peer]
PublicKey = Jk8P+8U3HCHPFGpNt/6bL6Ge3Ke0kM8mLg3UpSi4PDs=
AllowedIPs = 10.0.0.2/32
"""
        result = handler.validate_content(content)
        assert not result.valid
        assert any("Interface" in i.message for i in result.issues)

    def test_validate_missing_private_key(self):
        handler = WireguardHandler()
        content = """
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820

[Peer]
PublicKey = Jk8P+8U3HCHPFGpNt/6bL6Ge3Ke0kM8mLg3UpSi4PDs=
AllowedIPs = 10.0.0.2/32
"""
        result = handler.validate_content(content)
        assert not result.valid
        assert any("PrivateKey" in i.message for i in result.issues)

    def test_validate_missing_address(self):
        handler = WireguardHandler()
        content = """
[Interface]
PrivateKey = cPvPPFPQbLZ9stQnT5J+MdG7g5pmxcpNTm7tZHsCj1s=
ListenPort = 51820

[Peer]
PublicKey = Jk8P+8U3HCHPFGpNt/6bL6Ge3Ke0kM8mLg3UpSi4PDs=
AllowedIPs = 10.0.0.2/32
"""
        result = handler.validate_content(content)
        assert not result.valid
        assert any("Address" in i.message for i in result.issues)

    def test_validate_peer_missing_public_key(self):
        handler = WireguardHandler()
        content = """
[Interface]
PrivateKey = cPvPPFPQbLZ9stQnT5J+MdG7g5pmxcpNTm7tZHsCj1s=
Address = 10.0.0.1/24

[Peer]
AllowedIPs = 10.0.0.2/32
"""
        result = handler.validate_content(content)
        assert not result.valid
        assert any("PublicKey" in i.message for i in result.issues)

    def test_validate_peer_missing_allowed_ips(self):
        handler = WireguardHandler()
        content = """
[Interface]
PrivateKey = cPvPPFPQbLZ9stQnT5J+MdG7g5pmxcpNTm7tZHsCj1s=
Address = 10.0.0.1/24

[Peer]
PublicKey = Jk8P+8U3HCHPFGpNt/6bL6Ge3Ke0kM8mLg3UpSi4PDs=
"""
        result = handler.validate_content(content)
        assert not result.valid
        assert any("AllowedIPs" in i.message for i in result.issues)

    def test_validate_invalid_key_format(self):
        handler = WireguardHandler()
        content = """
[Interface]
PrivateKey = not-a-valid-key
Address = 10.0.0.1/24

[Peer]
PublicKey = also-invalid
AllowedIPs = 10.0.0.2/32
"""
        result = handler.validate_content(content)
        assert not result.valid
        assert any("PrivateKey" in i.message or "PublicKey" in i.message for i in result.issues)

    def test_validate_invalid_port(self):
        handler = WireguardHandler()
        content = """
[Interface]
PrivateKey = cPvPPFPQbLZ9stQnT5J+MdG7g5pmxcpNTm7tZHsCj1s=
Address = 10.0.0.1/24
ListenPort = 99999
"""
        result = handler.validate_content(content)
        assert not result.valid
        assert any("port" in i.message.lower() for i in result.issues)

    def test_validate_privileged_port_warning(self):
        handler = WireguardHandler()
        content = """
[Interface]
PrivateKey = cPvPPFPQbLZ9stQnT5J+MdG7g5pmxcpNTm7tZHsCj1s=
Address = 10.0.0.1/24
ListenPort = 443
"""
        result = handler.validate_content(content)
        # Should have info about privileged port
        assert any("privileged" in i.message.lower() for i in result.issues)

    def test_validate_full_tunnel_info(self):
        handler = WireguardHandler()
        content = """
[Interface]
PrivateKey = cPvPPFPQbLZ9stQnT5J+MdG7g5pmxcpNTm7tZHsCj1s=
Address = 10.0.0.1/24

[Peer]
PublicKey = Jk8P+8U3HCHPFGpNt/6bL6Ge3Ke0kM8mLg3UpSi4PDs=
AllowedIPs = 0.0.0.0/0
"""
        result = handler.validate_content(content)
        assert result.valid
        # Should have info about full tunnel
        assert any("full tunnel" in i.message.lower() for i in result.issues)

    def test_validate_short_keepalive_warning(self):
        handler = WireguardHandler()
        content = """
[Interface]
PrivateKey = cPvPPFPQbLZ9stQnT5J+MdG7g5pmxcpNTm7tZHsCj1s=
Address = 10.0.0.1/24

[Peer]
PublicKey = Jk8P+8U3HCHPFGpNt/6bL6Ge3Ke0kM8mLg3UpSi4PDs=
AllowedIPs = 10.0.0.2/32
PersistentKeepalive = 5
"""
        result = handler.validate_content(content)
        assert result.valid
        # Should warn about short keepalive
        assert any("keepalive" in i.message.lower() for i in result.issues)

    def test_validate_multiple_peers(self):
        handler = WireguardHandler()
        content = """
[Interface]
PrivateKey = cPvPPFPQbLZ9stQnT5J+MdG7g5pmxcpNTm7tZHsCj1s=
Address = 10.0.0.1/24

[Peer]
PublicKey = Jk8P+8U3HCHPFGpNt/6bL6Ge3Ke0kM8mLg3UpSi4PDs=
AllowedIPs = 10.0.0.2/32

[Peer]
PublicKey = Xk9P+8U3HCHPFGpNt/6bL6Ge3Ke0kM8mLg3UpSi4PDs=
AllowedIPs = 10.0.0.3/32
"""
        result = handler.validate_content(content)
        assert result.valid

    def test_validate_no_peers_warning(self):
        handler = WireguardHandler()
        content = """
[Interface]
PrivateKey = cPvPPFPQbLZ9stQnT5J+MdG7g5pmxcpNTm7tZHsCj1s=
Address = 10.0.0.1/24
"""
        result = handler.validate_content(content)
        assert result.valid  # Valid but with warning
        assert any("Peer" in i.message for i in result.issues)

    def test_get_schema_hint(self):
        handler = WireguardHandler()
        schema = handler.get_schema_hint("/etc/wireguard/wg0.conf")
        assert "[Interface]" in schema
        assert "[Peer]" in schema
        assert "PrivateKey" in schema["[Interface]"]
        assert "PublicKey" in schema["[Peer]"]

    def test_get_template(self):
        handler = WireguardHandler()
        template = handler.get_template()
        assert "[Interface]" in template
        assert "PrivateKey" in template
        assert "[Peer]" in template
        assert "PublicKey" in template

    def test_validation_command(self):
        handler = WireguardHandler()
        cmd = handler.get_validation_command("/etc/wireguard/wg0.conf")
        assert "wg show wg0" in cmd


class TestWireguardKeyValidation:
    """Tests for WireGuard key validation helpers."""

    def test_valid_key_format(self):
        handler = WireguardHandler()
        # Valid 44-character base64 key
        assert handler._is_valid_wg_key("cPvPPFPQbLZ9stQnT5J+MdG7g5pmxcpNTm7tZHsCj1s=")

    def test_invalid_key_too_short(self):
        handler = WireguardHandler()
        assert not handler._is_valid_wg_key("short")

    def test_invalid_key_wrong_chars(self):
        handler = WireguardHandler()
        assert not handler._is_valid_wg_key("cPvPPFPQbLZ9stQnT5J+MdG7g5pmxcpNTm7tZHsCj!")

    def test_valid_cidr(self):
        handler = WireguardHandler()
        assert handler._is_valid_cidr("10.0.0.1/24")
        assert handler._is_valid_cidr("192.168.1.0/16")
        assert handler._is_valid_cidr("0.0.0.0/0")

    def test_invalid_cidr(self):
        handler = WireguardHandler()
        assert not handler._is_valid_cidr("10.0.0.1")  # No prefix
        assert not handler._is_valid_cidr("invalid")

    def test_valid_endpoint(self):
        handler = WireguardHandler()
        assert handler._is_valid_endpoint("vpn.example.com:51820")
        assert handler._is_valid_endpoint("192.168.1.1:51820")

    def test_invalid_endpoint(self):
        handler = WireguardHandler()
        assert not handler._is_valid_endpoint("vpn.example.com")  # No port
        assert not handler._is_valid_endpoint("vpn.example.com:99999")  # Invalid port
