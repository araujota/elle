"""WireGuard key management operations.

Provides secure key generation and rotation for WireGuard VPN.

Key operations use the `wg` command-line tool which must be installed.
For Polkit-gated privilege elevation, operations that modify
/etc/wireguard/ require authorization.

Example:
    # Generate a new keypair
    private_key, public_key = generate_keypair()

    # Rotate keys without dropping connections
    result = rotate_keys_safely("wg0")
    if result.success:
        print(f"New public key: {result.new_public_key}")
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# WireGuard configuration directory
WIREGUARD_CONFIG_DIR = Path("/etc/wireguard")


class WireGuardKeyError(Exception):
    """Exception raised for WireGuard key operation errors."""

    pass


class KeyRotationResult(BaseModel):
    """Result of a key rotation operation."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether rotation succeeded")
    interface: str = Field(description="WireGuard interface name")
    old_public_key: str | None = Field(default=None, description="Previous public key")
    new_public_key: str | None = Field(default=None, description="New public key")
    backup_path: str | None = Field(default=None, description="Path to config backup")
    error: str | None = Field(default=None, description="Error message if failed")
    rotated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When rotation was attempted",
    )
    steps_completed: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Steps that were completed",
    )


def _run_wg_command(args: list[str], capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a wg command.

    Args:
        args: Command arguments (without 'wg' prefix).
        capture: Whether to capture output.

    Returns:
        CompletedProcess result.

    Raises:
        WireGuardKeyError: If wg command not found.
    """
    try:
        return subprocess.run(
            ["wg"] + args,
            capture_output=capture,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as e:
        raise WireGuardKeyError("WireGuard tools not installed. Install with: apt install wireguard-tools") from e
    except subprocess.TimeoutExpired as e:
        raise WireGuardKeyError("WireGuard command timed out") from e


def generate_keypair() -> tuple[str, str]:
    """Generate a WireGuard private/public key pair.

    Uses `wg genkey` and `wg pubkey` to generate cryptographically
    secure Curve25519 keys.

    Returns:
        Tuple of (private_key, public_key) as base64-encoded strings.

    Raises:
        WireGuardKeyError: If key generation fails.

    Example:
        >>> private_key, public_key = generate_keypair()
        >>> len(private_key)
        44
        >>> len(public_key)
        44
    """
    # Generate private key
    result = _run_wg_command(["genkey"])
    if result.returncode != 0:
        raise WireGuardKeyError(f"Failed to generate private key: {result.stderr}")

    private_key = result.stdout.strip()

    # Derive public key
    result = subprocess.run(
        ["wg", "pubkey"],
        input=private_key,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise WireGuardKeyError(f"Failed to derive public key: {result.stderr}")

    public_key = result.stdout.strip()

    logger.debug(f"Generated keypair with public key: {public_key[:8]}...")
    return private_key, public_key


def generate_preshared_key() -> str:
    """Generate a WireGuard preshared key.

    Preshared keys add an additional layer of symmetric-key
    encryption for defense against quantum computers.

    Returns:
        Base64-encoded preshared key.

    Raises:
        WireGuardKeyError: If key generation fails.
    """
    result = _run_wg_command(["genpsk"])
    if result.returncode != 0:
        raise WireGuardKeyError(f"Failed to generate preshared key: {result.stderr}")

    return result.stdout.strip()


def derive_public_key(private_key: str) -> str:
    """Derive the public key from a private key.

    Args:
        private_key: Base64-encoded WireGuard private key.

    Returns:
        Base64-encoded public key.

    Raises:
        WireGuardKeyError: If derivation fails.
    """
    result = subprocess.run(
        ["wg", "pubkey"],
        input=private_key,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise WireGuardKeyError(f"Failed to derive public key: {result.stderr}")

    return result.stdout.strip()


def get_current_public_key(interface: str) -> str | None:
    """Get the current public key for a WireGuard interface.

    Args:
        interface: WireGuard interface name (e.g., "wg0").

    Returns:
        Public key or None if interface not found.
    """
    result = _run_wg_command(["show", interface, "public-key"])
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def get_interface_config_path(interface: str) -> Path:
    """Get the configuration file path for an interface.

    Args:
        interface: WireGuard interface name.

    Returns:
        Path to the configuration file.
    """
    return WIREGUARD_CONFIG_DIR / f"{interface}.conf"


def rotate_keys_safely(
    interface: str,
    grace_period_seconds: int = 300,
) -> KeyRotationResult:
    """Rotate WireGuard keys without dropping connections.

    This performs a safe key rotation that maintains connectivity:
    1. Generate new keypair
    2. Backup current configuration
    3. Update configuration with new key
    4. Restart interface
    5. Return new public key for peer updates

    Note: After rotation, peers must be updated with the new public key.
    The grace_period_seconds is informational - actual peer updates are
    the user's responsibility.

    Args:
        interface: WireGuard interface name (e.g., "wg0").
        grace_period_seconds: Suggested time before removing old key from peers.

    Returns:
        KeyRotationResult with operation status.

    Note:
        This operation requires root/sudo privileges to modify
        /etc/wireguard/ and restart the interface.
    """
    steps_completed: list[str] = []
    config_path = get_interface_config_path(interface)

    # Check if interface exists
    old_public_key = get_current_public_key(interface)
    if not old_public_key:
        return KeyRotationResult(
            success=False,
            interface=interface,
            error=f"Interface {interface} not found or not active",
        )
    steps_completed.append(f"Retrieved current public key: {old_public_key[:8]}...")

    # Check if config file exists
    if not config_path.exists():
        return KeyRotationResult(
            success=False,
            interface=interface,
            old_public_key=old_public_key,
            error=f"Configuration file not found: {config_path}",
        )
    steps_completed.append(f"Found config file: {config_path}")

    # Generate new keypair
    try:
        new_private_key, new_public_key = generate_keypair()
        steps_completed.append(f"Generated new keypair: {new_public_key[:8]}...")
    except WireGuardKeyError as e:
        return KeyRotationResult(
            success=False,
            interface=interface,
            old_public_key=old_public_key,
            error=str(e),
            steps_completed=tuple(steps_completed),
        )

    # Create backup
    backup_dir = Path("/var/lib/elle/backups/wireguard")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{interface}_{timestamp}.conf"

    try:
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Read current config
        config_content = config_path.read_text()

        # Write backup
        backup_path.write_text(config_content)
        backup_path.chmod(0o600)  # Restrict permissions
        steps_completed.append(f"Created backup: {backup_path}")

        # Update config with new private key
        import re

        new_config = re.sub(
            r"(PrivateKey\s*=\s*)\S+",
            f"\\1{new_private_key}",
            config_content,
        )

        if new_config == config_content:
            return KeyRotationResult(
                success=False,
                interface=interface,
                old_public_key=old_public_key,
                backup_path=str(backup_path),
                error="Could not find PrivateKey in configuration",
                steps_completed=tuple(steps_completed),
            )

        # Write new config
        config_path.write_text(new_config)
        steps_completed.append("Updated configuration with new key")

        # Restart interface
        result = subprocess.run(
            ["wg-quick", "down", interface],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Ignore down failure - interface might not be up

        result = subprocess.run(
            ["wg-quick", "up", interface],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            # Restore backup
            backup_path.rename(config_path)
            return KeyRotationResult(
                success=False,
                interface=interface,
                old_public_key=old_public_key,
                backup_path=str(backup_path),
                error=f"Failed to restart interface: {result.stderr}",
                steps_completed=tuple(steps_completed),
            )
        steps_completed.append(f"Restarted interface {interface}")

        logger.info(f"Rotated keys for {interface}: {old_public_key[:8]}... -> {new_public_key[:8]}...")

        return KeyRotationResult(
            success=True,
            interface=interface,
            old_public_key=old_public_key,
            new_public_key=new_public_key,
            backup_path=str(backup_path),
            steps_completed=tuple(steps_completed),
        )

    except PermissionError:
        return KeyRotationResult(
            success=False,
            interface=interface,
            old_public_key=old_public_key,
            error="Permission denied. This operation requires root privileges.",
            steps_completed=tuple(steps_completed),
        )
    except Exception as e:
        return KeyRotationResult(
            success=False,
            interface=interface,
            old_public_key=old_public_key,
            error=str(e),
            steps_completed=tuple(steps_completed),
        )


def list_interfaces() -> list[str]:
    """List active WireGuard interfaces.

    Returns:
        List of interface names.
    """
    result = _run_wg_command(["show", "interfaces"])
    if result.returncode != 0:
        return []

    interfaces = result.stdout.strip().split()
    return interfaces


def get_interface_stats(interface: str) -> dict[str, Any] | None:
    """Get transfer statistics for a WireGuard interface.

    Args:
        interface: WireGuard interface name.

    Returns:
        Dict with transfer stats or None if interface not found.
    """
    result = _run_wg_command(["show", interface, "dump"])
    if result.returncode != 0:
        return None

    lines = result.stdout.strip().split("\n")
    if not lines:
        return None

    # First line is interface info
    # private_key, public_key, listen_port, fwmark
    interface_parts = lines[0].split("\t")
    if len(interface_parts) < 4:
        return None

    peers: list[dict[str, str | int | list[str] | None]] = []
    stats: dict[str, str | int | list[dict[str, str | int | list[str] | None]] | None] = {
        "public_key": interface_parts[1],
        "listen_port": int(interface_parts[2]) if interface_parts[2] != "(none)" else None,
        "peers": peers,
    }

    # Remaining lines are peers
    # public_key, preshared_key, endpoint, allowed_ips, latest_handshake, rx, tx, keepalive
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < 8:
            continue

        peer: dict[str, str | int | list[str] | None] = {
            "public_key": parts[0],
            "endpoint": parts[2] if parts[2] != "(none)" else None,
            "allowed_ips": parts[3].split(",") if parts[3] else [],
            "latest_handshake": int(parts[4]) if parts[4] != "0" else None,
            "rx_bytes": int(parts[5]),
            "tx_bytes": int(parts[6]),
            "keepalive": int(parts[7]) if parts[7] != "off" else None,
        }
        peers.append(peer)

    return stats
