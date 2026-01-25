"""WireGuard domain handler for configuration generation.

Handles WireGuard VPN configuration files in /etc/wireguard/*.conf.
Provides natural language generation and validation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from elle.rag.confgen.domains import DomainHandler
from elle.rag.confgen.models import (
    ConfigDomain,
    ConfigFileType,
    ConfigGenValidation,
    ValidationIssue,
)


class WireguardHandler(DomainHandler):
    """Handler for WireGuard VPN configuration.

    Supports generation and validation of:
    - Interface configuration (wg0.conf, etc.)
    - Peer configuration
    - Key management validation

    Usage:
        handler = WireguardHandler()
        if handler.matches_path("/etc/wireguard/wg0.conf"):
            validation = handler.validate_content(content)
    """

    @property
    def domain(self) -> ConfigDomain:
        return "wireguard"

    @property
    def file_patterns(self) -> tuple[str, ...]:
        return (
            "/etc/wireguard/*.conf",
            "/etc/wireguard/**/*.conf",
        )

    @property
    def file_type(self) -> ConfigFileType:
        return "text"  # INI-like format

    def matches_path(self, path: Path | str) -> bool:
        """Check if a path matches WireGuard configuration patterns."""
        path = Path(path)

        # Check if in /etc/wireguard/
        if "/etc/wireguard" in str(path) and path.suffix == ".conf":
            return True

        # Check filename pattern
        if path.name.startswith("wg") and path.suffix == ".conf":
            return True

        return False

    def get_schema_hint(self, file_path: Path | str) -> dict[str, Any] | None:
        """Get schema hints for WireGuard configuration."""
        return {
            "[Interface]": {
                "PrivateKey": "<base64-encoded private key>",
                "Address": "<IP/CIDR, e.g., 10.0.0.1/24>",
                "ListenPort": "<UDP port, e.g., 51820>",
                "DNS": "<DNS server IP>",
                "PostUp": "<command to run after interface up>",
                "PostDown": "<command to run after interface down>",
                "MTU": "<MTU size, e.g., 1420>",
                "SaveConfig": "true|false",
            },
            "[Peer]": {
                "PublicKey": "<base64-encoded public key>",
                "AllowedIPs": "<IP/CIDR ranges, e.g., 10.0.0.0/24, 0.0.0.0/0>",
                "Endpoint": "<host:port>",
                "PersistentKeepalive": "<seconds, e.g., 25>",
                "PresharedKey": "<optional base64-encoded PSK>",
            },
        }

    def validate_content(self, content: str) -> ConfigGenValidation:
        """Validate WireGuard configuration content."""
        issues: list[ValidationIssue] = []

        lines = content.split("\n")
        current_section = None
        has_interface = False
        has_private_key = False
        has_address = False
        peer_count = 0
        peer_has_public_key = False
        peer_has_allowed_ips = False

        for i, line in enumerate(lines, 1):
            line = line.strip()

            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue

            # Check for section headers
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].lower()

                # Validate previous peer section if any
                if current_section == "peer":
                    if not peer_has_public_key:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                message=f"Peer {peer_count} missing PublicKey",
                            )
                        )
                    if not peer_has_allowed_ips:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                message=f"Peer {peer_count} missing AllowedIPs",
                            )
                        )

                if section == "interface":
                    if has_interface:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                message="Multiple [Interface] sections not allowed",
                            )
                        )
                    has_interface = True
                    current_section = "interface"
                elif section == "peer":
                    peer_count += 1
                    peer_has_public_key = False
                    peer_has_allowed_ips = False
                    current_section = "peer"
                else:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            message=f"Line {i}: Unknown section [{section}]",
                            suggestion="Valid sections are [Interface] and [Peer]",
                        )
                    )
                continue

            # Parse key-value pairs
            if "=" not in line:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        message=f"Line {i}: Invalid line format (expected key=value)",
                    )
                )
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            # Validate interface settings
            if current_section == "interface":
                if key.lower() == "privatekey":
                    has_private_key = True
                    if not self._is_valid_wg_key(value):
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                message=f"Line {i}: Invalid PrivateKey format",
                                suggestion="PrivateKey must be a valid base64-encoded WireGuard key",
                            )
                        )
                elif key.lower() == "address":
                    has_address = True
                    if not self._is_valid_cidr(value):
                        issues.append(
                            ValidationIssue(
                                severity="warning",
                                message=f"Line {i}: Address may be invalid: {value}",
                                suggestion="Use CIDR notation (e.g., 10.0.0.1/24)",
                            )
                        )
                elif key.lower() == "listenport":
                    try:
                        port = int(value)
                        if port < 1 or port > 65535:
                            issues.append(
                                ValidationIssue(
                                    severity="error",
                                    message=f"Line {i}: Invalid port number: {value}",
                                )
                            )
                        elif port < 1024:
                            issues.append(
                                ValidationIssue(
                                    severity="info",
                                    message=f"Line {i}: Privileged port {port} requires root",
                                )
                            )
                    except ValueError:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                message=f"Line {i}: ListenPort must be a number",
                            )
                        )
                elif key.lower() in ("postup", "postdown"):
                    # Check for potentially dangerous commands
                    if "rm " in value or ">" in value:
                        issues.append(
                            ValidationIssue(
                                severity="warning",
                                message=f"Line {i}: {key} contains potentially dangerous commands",
                                suggestion="Review commands carefully before use",
                            )
                        )

            # Validate peer settings
            elif current_section == "peer":
                if key.lower() == "publickey":
                    peer_has_public_key = True
                    if not self._is_valid_wg_key(value):
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                message=f"Line {i}: Invalid PublicKey format",
                                suggestion="PublicKey must be a valid base64-encoded WireGuard key",
                            )
                        )
                elif key.lower() == "allowedips":
                    peer_has_allowed_ips = True
                    for ip in value.split(","):
                        ip = ip.strip()
                        if ip and not self._is_valid_cidr(ip):
                            issues.append(
                                ValidationIssue(
                                    severity="warning",
                                    message=f"Line {i}: AllowedIPs may be invalid: {ip}",
                                )
                            )
                    # Check for full tunnel
                    if "0.0.0.0/0" in value or "::/0" in value:
                        issues.append(
                            ValidationIssue(
                                severity="info",
                                message=f"Line {i}: Full tunnel configured (all traffic routed)",
                            )
                        )
                elif key.lower() == "endpoint":
                    if not self._is_valid_endpoint(value):
                        issues.append(
                            ValidationIssue(
                                severity="warning",
                                message=f"Line {i}: Endpoint format may be invalid",
                                suggestion="Use format host:port (e.g., vpn.example.com:51820)",
                            )
                        )
                elif key.lower() == "persistentkeepalive":
                    try:
                        keepalive = int(value)
                        if keepalive < 0:
                            issues.append(
                                ValidationIssue(
                                    severity="error",
                                    message=f"Line {i}: PersistentKeepalive must be positive",
                                )
                            )
                        elif keepalive > 0 and keepalive < 15:
                            issues.append(
                                ValidationIssue(
                                    severity="warning",
                                    message=f"Line {i}: Very short keepalive ({keepalive}s) may cause excessive traffic",
                                    suggestion="Typical value is 25 seconds",
                                )
                            )
                    except ValueError:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                message=f"Line {i}: PersistentKeepalive must be a number",
                            )
                        )

        # Validate last peer section
        if current_section == "peer":
            if not peer_has_public_key:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Peer {peer_count} missing PublicKey",
                    )
                )
            if not peer_has_allowed_ips:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Peer {peer_count} missing AllowedIPs",
                    )
                )

        # Check for required fields
        if not has_interface:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message="Missing [Interface] section",
                )
            )
        if has_interface and not has_private_key:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message="[Interface] missing PrivateKey",
                    suggestion="Generate a key pair with: wg genkey | tee privatekey | wg pubkey > publickey",
                )
            )
        if has_interface and not has_address:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message="[Interface] missing Address",
                    suggestion="Add Address = <IP/CIDR> (e.g., Address = 10.0.0.1/24)",
                )
            )
        if peer_count == 0:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message="No [Peer] sections defined",
                    suggestion="Add at least one peer to establish a VPN tunnel",
                )
            )

        return ConfigGenValidation(
            valid=not any(i.severity == "error" for i in issues),
            issues=tuple(issues),
        )

    def _is_valid_wg_key(self, key: str) -> bool:
        """Check if a string looks like a valid WireGuard key.

        WireGuard keys are 32 bytes, base64-encoded to 44 characters.
        """
        if len(key) != 44:
            return False

        # Check base64 character set (with possible = padding)
        if not re.match(r"^[A-Za-z0-9+/]{43}=$", key):
            return False

        return True

    def _is_valid_cidr(self, cidr: str) -> bool:
        """Check if a string looks like a valid CIDR notation."""
        # IPv4 CIDR
        ipv4_pattern = r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}$"
        # IPv6 CIDR (simplified)
        ipv6_pattern = r"^[0-9a-fA-F:]+/\d{1,3}$"

        return bool(re.match(ipv4_pattern, cidr) or re.match(ipv6_pattern, cidr))

    def _is_valid_endpoint(self, endpoint: str) -> bool:
        """Check if a string looks like a valid endpoint (host:port)."""
        if ":" not in endpoint:
            return False

        parts = endpoint.rsplit(":", 1)
        if len(parts) != 2:
            return False

        try:
            port = int(parts[1])
            return 1 <= port <= 65535
        except ValueError:
            return False

    def get_template(self) -> str | None:
        """Get a template for WireGuard configuration."""
        return """[Interface]
# Generate with: wg genkey
PrivateKey = <YOUR_PRIVATE_KEY>
Address = 10.0.0.1/24
ListenPort = 51820

# Optional: DNS servers for the tunnel
# DNS = 1.1.1.1, 8.8.8.8

# Optional: Commands to run when interface goes up/down
# PostUp = iptables -A FORWARD -i %i -j ACCEPT
# PostDown = iptables -D FORWARD -i %i -j ACCEPT

[Peer]
# Peer's public key
PublicKey = <PEER_PUBLIC_KEY>
# Allowed IP ranges this peer can use
AllowedIPs = 10.0.0.2/32
# Peer's endpoint (if known/static)
# Endpoint = peer.example.com:51820
# Keep connection alive (useful behind NAT)
PersistentKeepalive = 25
"""

    def get_validation_command(self, file_path: Path | str) -> str | None:
        """Get command to validate WireGuard configuration."""
        path = Path(file_path)
        interface = path.stem  # e.g., wg0 from wg0.conf
        return f"wg show {interface}"
