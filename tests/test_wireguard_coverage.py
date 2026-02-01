"""Tests for WireGuard confgen handler coverage.

Covers missing lines for peer validation, multiple Interface sections,
unknown sections, invalid line format, address/ListenPort validation,
PostUp/PostDown dangerous commands, AllowedIPs full tunnel, invalid endpoint,
PersistentKeepalive validation, and file_patterns property.
"""

from __future__ import annotations

from elle.rag.confgen.wireguard import WireguardHandler

# Valid base64-encoded 44-char WireGuard key for test use
VALID_KEY = "cPvPPFPQbLZ9stQnT5J+MdG7g5pmxcpNTm7tZHsCj1s="
VALID_PUBKEY = "Jk8P+8U3HCHPFGpNt/6bL6Ge3Ke0kM8mLg3UpSi4PDs="


def _make_handler():
    return WireguardHandler()


class TestFilePatterns:
    """Tests for the file_patterns property."""

    def test_file_patterns_returns_tuple(self):
        handler = _make_handler()
        patterns = handler.file_patterns
        assert isinstance(patterns, tuple)
        assert len(patterns) >= 2
        assert any("/etc/wireguard" in p for p in patterns)


class TestPeerValidation:
    """Tests for peer section validation: missing PublicKey and AllowedIPs."""

    def test_peer_missing_public_key_at_section_boundary(self):
        """Peer without PublicKey detected when next section starts."""
        handler = _make_handler()
        content = f"""[Interface]
PrivateKey = {VALID_KEY}
Address = 10.0.0.1/24

[Peer]
AllowedIPs = 10.0.0.2/32

[Peer]
PublicKey = {VALID_PUBKEY}
AllowedIPs = 10.0.0.3/32
"""
        result = handler.validate_content(content)
        assert not result.valid
        msgs = [i.message for i in result.issues]
        assert any("Peer 1 missing PublicKey" in m for m in msgs)

    def test_peer_missing_allowed_ips_at_section_boundary(self):
        """Peer without AllowedIPs detected when next section starts."""
        handler = _make_handler()
        content = f"""[Interface]
PrivateKey = {VALID_KEY}
Address = 10.0.0.1/24

[Peer]
PublicKey = {VALID_PUBKEY}

[Peer]
PublicKey = {VALID_PUBKEY}
AllowedIPs = 10.0.0.3/32
"""
        result = handler.validate_content(content)
        assert not result.valid
        msgs = [i.message for i in result.issues]
        assert any("Peer 1 missing AllowedIPs" in m for m in msgs)


class TestMultipleInterfaceSections:
    """Tests for multiple [Interface] sections."""

    def test_multiple_interface_error(self):
        handler = _make_handler()
        content = f"""[Interface]
PrivateKey = {VALID_KEY}
Address = 10.0.0.1/24

[Interface]
PrivateKey = {VALID_KEY}
Address = 10.0.0.2/24

[Peer]
PublicKey = {VALID_PUBKEY}
AllowedIPs = 10.0.0.3/32
"""
        result = handler.validate_content(content)
        assert not result.valid
        msgs = [i.message for i in result.issues]
        assert any("Multiple [Interface]" in m for m in msgs)


class TestUnknownSection:
    """Tests for unknown section headers."""

    def test_unknown_section_error(self):
        handler = _make_handler()
        content = f"""[Interface]
PrivateKey = {VALID_KEY}
Address = 10.0.0.1/24

[Unknown]
SomeKey = value

[Peer]
PublicKey = {VALID_PUBKEY}
AllowedIPs = 10.0.0.2/32
"""
        result = handler.validate_content(content)
        assert not result.valid
        msgs = [i.message for i in result.issues]
        assert any("Unknown section" in m for m in msgs)


class TestInvalidLineFormat:
    """Tests for invalid line format (no = sign)."""

    def test_invalid_line_warning(self):
        handler = _make_handler()
        content = f"""[Interface]
PrivateKey = {VALID_KEY}
Address = 10.0.0.1/24
this line has no equals sign

[Peer]
PublicKey = {VALID_PUBKEY}
AllowedIPs = 10.0.0.2/32
"""
        result = handler.validate_content(content)
        msgs = [i.message for i in result.issues]
        assert any("Invalid line format" in m for m in msgs)


class TestAddressValidation:
    """Tests for Address CIDR validation."""

    def test_invalid_address_warning(self):
        handler = _make_handler()
        content = f"""[Interface]
PrivateKey = {VALID_KEY}
Address = not-a-cidr

[Peer]
PublicKey = {VALID_PUBKEY}
AllowedIPs = 10.0.0.2/32
"""
        result = handler.validate_content(content)
        msgs = [i.message for i in result.issues]
        assert any("Address may be invalid" in m for m in msgs)


class TestListenPortValidation:
    """Tests for ListenPort validation."""

    def test_listen_port_not_a_number(self):
        handler = _make_handler()
        content = f"""[Interface]
PrivateKey = {VALID_KEY}
Address = 10.0.0.1/24
ListenPort = abc

[Peer]
PublicKey = {VALID_PUBKEY}
AllowedIPs = 10.0.0.2/32
"""
        result = handler.validate_content(content)
        assert not result.valid
        msgs = [i.message for i in result.issues]
        assert any("must be a number" in m for m in msgs)


class TestPostUpPostDownDangerous:
    """Tests for dangerous PostUp/PostDown commands."""

    def test_postup_dangerous_rm(self):
        handler = _make_handler()
        content = f"""[Interface]
PrivateKey = {VALID_KEY}
Address = 10.0.0.1/24
PostUp = rm -rf /tmp/something

[Peer]
PublicKey = {VALID_PUBKEY}
AllowedIPs = 10.0.0.2/32
"""
        result = handler.validate_content(content)
        msgs = [i.message for i in result.issues]
        assert any("dangerous" in m.lower() for m in msgs)

    def test_postdown_dangerous_redirect(self):
        handler = _make_handler()
        content = f"""[Interface]
PrivateKey = {VALID_KEY}
Address = 10.0.0.1/24
PostDown = echo test > /dev/null

[Peer]
PublicKey = {VALID_PUBKEY}
AllowedIPs = 10.0.0.2/32
"""
        result = handler.validate_content(content)
        msgs = [i.message for i in result.issues]
        assert any("dangerous" in m.lower() for m in msgs)


class TestAllowedIPsFullTunnel:
    """Tests for AllowedIPs full tunnel detection."""

    def test_ipv6_full_tunnel(self):
        handler = _make_handler()
        content = f"""[Interface]
PrivateKey = {VALID_KEY}
Address = 10.0.0.1/24

[Peer]
PublicKey = {VALID_PUBKEY}
AllowedIPs = ::/0
"""
        result = handler.validate_content(content)
        msgs = [i.message for i in result.issues]
        assert any("full tunnel" in m.lower() for m in msgs)


class TestInvalidEndpoint:
    """Tests for invalid endpoint format."""

    def test_invalid_endpoint_warning(self):
        handler = _make_handler()
        content = f"""[Interface]
PrivateKey = {VALID_KEY}
Address = 10.0.0.1/24

[Peer]
PublicKey = {VALID_PUBKEY}
AllowedIPs = 10.0.0.2/32
Endpoint = not-valid-endpoint
"""
        result = handler.validate_content(content)
        msgs = [i.message for i in result.issues]
        assert any("Endpoint" in m for m in msgs)


class TestPersistentKeepaliveValidation:
    """Tests for PersistentKeepalive validation."""

    def test_negative_keepalive(self):
        handler = _make_handler()
        content = f"""[Interface]
PrivateKey = {VALID_KEY}
Address = 10.0.0.1/24

[Peer]
PublicKey = {VALID_PUBKEY}
AllowedIPs = 10.0.0.2/32
PersistentKeepalive = -1
"""
        result = handler.validate_content(content)
        assert not result.valid
        msgs = [i.message for i in result.issues]
        assert any("must be positive" in m for m in msgs)

    def test_keepalive_not_a_number(self):
        handler = _make_handler()
        content = f"""[Interface]
PrivateKey = {VALID_KEY}
Address = 10.0.0.1/24

[Peer]
PublicKey = {VALID_PUBKEY}
AllowedIPs = 10.0.0.2/32
PersistentKeepalive = abc
"""
        result = handler.validate_content(content)
        assert not result.valid
        msgs = [i.message for i in result.issues]
        assert any("must be a number" in m for m in msgs)
