"""Network operation capabilities.

Provides typed, policy-governed operations for network management:
- wireguard.restart - Restart WireGuard interface
- wireguard.status - Check WireGuard interface status
- network.listeners - List processes listening on ports
"""

from __future__ import annotations

import contextlib
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from elle.capabilities.models import (
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    RollbackResult,
    SideEffect,
    VerificationResult,
)
from elle.capabilities.protocol import BaseCapability

logger = logging.getLogger(__name__)


# =============================================================================
# Helper functions
# =============================================================================


def _run_command(
    cmd: list[str],
    timeout: int = 30,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command safely.

    Args:
        cmd: Command and arguments.
        timeout: Command timeout in seconds.
        check: Whether to raise on non-zero exit.

    Returns:
        CompletedProcess with stdout/stderr.
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def _is_wireguard_available() -> bool:
    """Check if wg-quick is available."""
    try:
        result = _run_command(["which", "wg-quick"], timeout=5)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _interface_exists(interface: str) -> bool:
    """Check if a network interface exists."""
    try:
        result = _run_command(["ip", "link", "show", interface], timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def _parse_wg_show(output: str) -> dict[str, Any]:
    """Parse output from 'wg show' command.

    Args:
        output: Raw wg show output.

    Returns:
        Parsed data dict.
    """
    data: dict[str, Any] = {
        "interface": None,
        "public_key": None,
        "private_key": None,
        "listening_port": None,
        "peers": [],
    }

    current_peer: dict[str, Any] | None = None

    for line in output.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        # Interface line
        if line.startswith("interface:"):
            data["interface"] = line.split(":", 1)[1].strip()
        elif line.startswith("public key:"):
            data["public_key"] = line.split(":", 1)[1].strip()
        elif line.startswith("private key:"):
            data["private_key"] = line.split(":", 1)[1].strip()
        elif line.startswith("listening port:"):
            with contextlib.suppress(ValueError):
                data["listening_port"] = int(line.split(":", 1)[1].strip())
        elif line.startswith("peer:"):
            if current_peer:
                data["peers"].append(current_peer)
            current_peer = {
                "public_key": line.split(":", 1)[1].strip(),
                "endpoint": None,
                "allowed_ips": [],
                "latest_handshake": None,
                "transfer_rx": 0,
                "transfer_tx": 0,
            }
        elif current_peer:
            if line.startswith("endpoint:"):
                current_peer["endpoint"] = line.split(":", 1)[1].strip()
            elif line.startswith("allowed ips:"):
                ips = line.split(":", 1)[1].strip()
                current_peer["allowed_ips"] = [ip.strip() for ip in ips.split(",")]
            elif line.startswith("latest handshake:"):
                current_peer["latest_handshake"] = line.split(":", 1)[1].strip()
            elif line.startswith("transfer:"):
                transfer = line.split(":", 1)[1].strip()
                # Parse "X received, Y sent"
                match = re.match(r"(\S+)\s+received,\s+(\S+)\s+sent", transfer)
                if match:
                    current_peer["transfer_rx"] = match.group(1)
                    current_peer["transfer_tx"] = match.group(2)

    if current_peer:
        data["peers"].append(current_peer)

    return data


# =============================================================================
# Input/Output Models
# =============================================================================


class WireGuardRestartInput(BaseModel):
    """Input for wireguard.restart operation."""

    model_config = ConfigDict(frozen=True)

    interface: str = Field(
        default="wg0",
        description="WireGuard interface name",
        pattern=r"^wg\d+$",
    )
    verify_handshake: bool = Field(
        default=True,
        description="Verify handshake after restart",
    )
    handshake_timeout_sec: int = Field(
        default=30,
        ge=5,
        le=120,
        description="Timeout waiting for handshake verification",
    )


class WireGuardRestartOutput(BaseModel):
    """Output from wireguard.restart operation."""

    model_config = ConfigDict(frozen=True)

    interface: str = Field(description="Interface that was restarted")
    was_up: bool = Field(description="Whether interface was up before restart")
    now_up: bool = Field(description="Whether interface is up after restart")
    handshake_verified: bool = Field(
        default=False,
        description="Whether handshake was verified after restart",
    )
    handshake_age_sec: int | None = Field(
        default=None,
        description="Age of latest handshake in seconds",
    )


class WireGuardStatusInput(BaseModel):
    """Input for wireguard.status operation."""

    model_config = ConfigDict(frozen=True)

    interface: str = Field(
        default="wg0",
        description="WireGuard interface name",
        pattern=r"^wg\d+$",
    )
    check_routing: bool = Field(
        default=True,
        description="Also check if routes are configured",
    )


class PeerStatus(BaseModel):
    """Status of a WireGuard peer."""

    model_config = ConfigDict(frozen=True)

    public_key: str = Field(description="Peer public key (truncated)")
    endpoint: str | None = Field(default=None, description="Peer endpoint")
    allowed_ips: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Allowed IP ranges",
    )
    latest_handshake: str | None = Field(
        default=None,
        description="Latest handshake time string",
    )
    handshake_age_sec: int | None = Field(
        default=None,
        description="Handshake age in seconds",
    )
    transfer_rx: str = Field(default="0", description="Received data")
    transfer_tx: str = Field(default="0", description="Sent data")
    is_stale: bool = Field(
        default=False,
        description="Whether handshake is stale (>2 min)",
    )


class WireGuardStatusOutput(BaseModel):
    """Output from wireguard.status operation."""

    model_config = ConfigDict(frozen=True)

    interface: str = Field(description="Interface name")
    is_up: bool = Field(description="Whether interface is up")
    public_key: str | None = Field(default=None, description="Interface public key")
    listening_port: int | None = Field(default=None, description="Listening port")
    peers: tuple[PeerStatus, ...] = Field(
        default_factory=tuple,
        description="Connected peers",
    )
    has_routes: bool = Field(
        default=False,
        description="Whether routes are configured",
    )
    any_stale_handshakes: bool = Field(
        default=False,
        description="Whether any peer has stale handshake",
    )


class NetworkListenersInput(BaseModel):
    """Input for network.listeners operation."""

    model_config = ConfigDict(frozen=True)

    port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description="Filter by specific port (None for all)",
    )
    protocol: str | None = Field(
        default=None,
        description="Filter by protocol (tcp, udp, or None for both)",
    )
    include_loopback: bool = Field(
        default=False,
        description="Include loopback-only listeners",
    )


class ListenerInfo(BaseModel):
    """Information about a listening socket."""

    model_config = ConfigDict(frozen=True)

    port: int = Field(description="Port number")
    protocol: str = Field(description="Protocol (tcp/udp)")
    address: str = Field(description="Listening address")
    pid: int | None = Field(default=None, description="Process ID")
    process_name: str = Field(default="unknown", description="Process name")
    is_wildcard: bool = Field(
        default=False,
        description="Whether listening on 0.0.0.0 or [::]",
    )


class NetworkListenersOutput(BaseModel):
    """Output from network.listeners operation."""

    model_config = ConfigDict(frozen=True)

    listeners: tuple[ListenerInfo, ...] = Field(
        default_factory=tuple,
        description="List of listeners",
    )
    total: int = Field(default=0, description="Total listener count")


# =============================================================================
# WireGuard Restart Capability
# =============================================================================


class WireGuardRestartCapability(BaseCapability[WireGuardRestartInput, WireGuardRestartOutput]):
    """Restart a WireGuard interface."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="wireguard.restart",
            summary="Restart WireGuard interface and verify handshake",
            domain="network",
            risk="medium",
            side_effects=(
                SideEffect(
                    kind="network_change",
                    target="{interface}",
                    reversible=True,
                    description="WireGuard interface will be restarted",
                ),
            ),
            requires_privilege=True,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="WireGuardRestartInput",
            output_schema_name="WireGuardRestartOutput",
        )

    def dry_run(self, input: WireGuardRestartInput) -> DryRunResult:
        """Preview WireGuard restart."""
        if not _is_wireguard_available():
            return DryRunResult(
                is_valid=False,
                validation_errors=("wg-quick is not available",),
                preview_text="Cannot restart: wg-quick not found",
            )

        # Check if config exists
        config_path = Path(f"/etc/wireguard/{input.interface}.conf")
        if not config_path.exists():
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"Config not found: {config_path}",),
                preview_text=f"Cannot restart: no config for {input.interface}",
            )

        was_up = _interface_exists(input.interface)

        return DryRunResult(
            would_execute=(
                f"wg-quick down {input.interface}",
                f"wg-quick up {input.interface}",
            ),
            would_modify=(input.interface,),
            estimated_risk="medium",
            requires_confirmation=True,
            preview_text=f"Would restart WireGuard interface '{input.interface}' "
            f"({'currently up' if was_up else 'currently down'})",
            is_valid=True,
        )

    def run(self, input: WireGuardRestartInput) -> CapabilityResult:
        """Restart the WireGuard interface."""
        start_time = time.time()

        if not _is_wireguard_available():
            return CapabilityResult(
                success=False,
                error="wg-quick is not available",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        commands_executed: list[str] = []
        was_up = _interface_exists(input.interface)

        try:
            # Bring down if up
            if was_up:
                result = _run_command(
                    ["sudo", "wg-quick", "down", input.interface],
                    timeout=30,
                )
                commands_executed.append(f"wg-quick down {input.interface}")
                if result.returncode != 0:
                    logger.warning(f"wg-quick down returned: {result.stderr}")

            # Bring up
            result = _run_command(
                ["sudo", "wg-quick", "up", input.interface],
                timeout=30,
            )
            commands_executed.append(f"wg-quick up {input.interface}")

            if result.returncode != 0:
                return CapabilityResult(
                    success=False,
                    error=f"wg-quick up failed: {result.stderr}",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    evidence=CapabilityEvidence(
                        commands_executed=tuple(commands_executed),
                    ),
                )

            now_up = _interface_exists(input.interface)

            # Verify handshake if requested
            handshake_verified = False
            handshake_age_sec = None

            if input.verify_handshake and now_up:
                deadline = time.time() + input.handshake_timeout_sec

                while time.time() < deadline:
                    # Check handshake status
                    wg_result = _run_command(
                        ["sudo", "wg", "show", input.interface],
                        timeout=10,
                    )

                    if wg_result.returncode == 0:
                        data = _parse_wg_show(wg_result.stdout)

                        # Check if any peer has recent handshake
                        for peer in data.get("peers", []):
                            hs = peer.get("latest_handshake")
                            if hs and hs != "(none)":
                                # Parse handshake age
                                age = self._parse_handshake_age(hs)
                                if age is not None and age < 120:
                                    handshake_verified = True
                                    handshake_age_sec = age
                                    break

                    if handshake_verified:
                        break

                    time.sleep(2)

            return CapabilityResult(
                success=True,
                output=WireGuardRestartOutput(
                    interface=input.interface,
                    was_up=was_up,
                    now_up=now_up,
                    handshake_verified=handshake_verified,
                    handshake_age_sec=handshake_age_sec,
                ),
                side_effects_applied=(
                    SideEffect(
                        kind="network_change",
                        target=input.interface,
                        reversible=True,
                        description=f"Restarted WireGuard interface {input.interface}",
                    ),
                ),
                warnings=() if handshake_verified else ("Handshake not verified within timeout",),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    commands_executed=tuple(commands_executed),
                    rationale=f"Restarted WireGuard interface '{input.interface}'",
                ),
            )

        except subprocess.TimeoutExpired:
            return CapabilityResult(
                success=False,
                error="WireGuard command timed out",
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    commands_executed=tuple(commands_executed),
                ),
            )
        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

    def _parse_handshake_age(self, hs_str: str) -> int | None:
        """Parse handshake age string to seconds.

        Args:
            hs_str: Handshake string like "1 minute, 23 seconds ago"

        Returns:
            Age in seconds or None if unparseable.
        """
        if not hs_str or hs_str == "(none)":
            return None

        total_seconds = 0

        # Match patterns like "1 hour", "2 minutes", "30 seconds"
        hour_match = re.search(r"(\d+)\s*hours?", hs_str)
        min_match = re.search(r"(\d+)\s*minutes?", hs_str)
        sec_match = re.search(r"(\d+)\s*seconds?", hs_str)

        if hour_match:
            total_seconds += int(hour_match.group(1)) * 3600
        if min_match:
            total_seconds += int(min_match.group(1)) * 60
        if sec_match:
            total_seconds += int(sec_match.group(1))

        return total_seconds if total_seconds > 0 else None

    def verify(self, input: WireGuardRestartInput) -> VerificationResult:
        """Verify interface is up."""
        is_up = _interface_exists(input.interface)

        return VerificationResult(
            passed=is_up,
            checks_performed=("interface up",),
            actual_state={"is_up": is_up},
            expected_state={"is_up": True},
            discrepancies=() if is_up else (f"Interface {input.interface} is not up",),
        )

    def rollback(
        self,
        input: WireGuardRestartInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback by bringing interface to previous state."""
        if not result.output:
            return RollbackResult(
                success=False,
                error="No output to determine previous state",
            )

        if not result.output.was_up:
            # Interface was down before, bring it down again
            try:
                _run_command(["sudo", "wg-quick", "down", input.interface])
                return RollbackResult(
                    success=True,
                    rolled_back=(input.interface,),
                    commands_executed=(f"wg-quick down {input.interface}",),
                )
            except Exception as e:
                return RollbackResult(
                    success=False,
                    error=str(e),
                    failed_to_rollback=(input.interface,),
                )

        # Interface was up before and is up now, nothing to rollback
        return RollbackResult(
            success=True,
            rolled_back=(),
        )


# =============================================================================
# WireGuard Status Capability
# =============================================================================


class WireGuardStatusCapability(BaseCapability[WireGuardStatusInput, WireGuardStatusOutput]):
    """Check WireGuard interface status."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="wireguard.status",
            summary="Check WireGuard interface status, peer handshakes, and routing",
            domain="network",
            risk="none",
            side_effects=(),
            requires_privilege=True,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="WireGuardStatusInput",
            output_schema_name="WireGuardStatusOutput",
        )

    def dry_run(self, input: WireGuardStatusInput) -> DryRunResult:
        """Preview WireGuard status check."""
        if not _is_wireguard_available():
            return DryRunResult(
                is_valid=False,
                validation_errors=("wg-quick is not available",),
                preview_text="Cannot check status: wg-quick not found",
            )

        return DryRunResult(
            would_execute=(f"wg show {input.interface}",),
            would_modify=(),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text=f"Would check status of WireGuard interface '{input.interface}'",
            is_valid=True,
        )

    def run(self, input: WireGuardStatusInput) -> CapabilityResult:
        """Check WireGuard status."""
        start_time = time.time()

        if not _is_wireguard_available():
            return CapabilityResult(
                success=False,
                error="wg-quick is not available",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        is_up = _interface_exists(input.interface)

        if not is_up:
            return CapabilityResult(
                success=True,
                output=WireGuardStatusOutput(
                    interface=input.interface,
                    is_up=False,
                    public_key=None,
                    listening_port=None,
                    peers=(),
                    has_routes=False,
                    any_stale_handshakes=False,
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Interface '{input.interface}' is down",
                ),
            )

        try:
            # Get WireGuard info
            result = _run_command(
                ["sudo", "wg", "show", input.interface],
                timeout=10,
            )

            if result.returncode != 0:
                return CapabilityResult(
                    success=False,
                    error=f"wg show failed: {result.stderr}",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            data = _parse_wg_show(result.stdout)

            # Build peer statuses
            peers: list[PeerStatus] = []
            any_stale = False

            for peer_data in data.get("peers", []):
                hs = peer_data.get("latest_handshake")
                hs_age = self._parse_handshake_age(hs) if hs else None
                is_stale = hs_age is None or hs_age > 120

                if is_stale:
                    any_stale = True

                peers.append(
                    PeerStatus(
                        public_key=peer_data.get("public_key", "")[:20] + "...",
                        endpoint=peer_data.get("endpoint"),
                        allowed_ips=tuple(peer_data.get("allowed_ips", [])),
                        latest_handshake=hs,
                        handshake_age_sec=hs_age,
                        transfer_rx=peer_data.get("transfer_rx", "0"),
                        transfer_tx=peer_data.get("transfer_tx", "0"),
                        is_stale=is_stale,
                    )
                )

            # Check routes if requested
            has_routes = False
            if input.check_routing:
                route_result = _run_command(
                    ["ip", "route", "show", "dev", input.interface],
                    timeout=5,
                )
                has_routes = bool(route_result.stdout.strip())

            return CapabilityResult(
                success=True,
                output=WireGuardStatusOutput(
                    interface=input.interface,
                    is_up=is_up,
                    public_key=data.get("public_key"),
                    listening_port=data.get("listening_port"),
                    peers=tuple(peers),
                    has_routes=has_routes,
                    any_stale_handshakes=any_stale,
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    commands_executed=(f"wg show {input.interface}",),
                    rationale=f"Checked status of '{input.interface}'",
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

    def _parse_handshake_age(self, hs_str: str) -> int | None:
        """Parse handshake age string to seconds."""
        if not hs_str or hs_str == "(none)":
            return None

        total_seconds = 0

        hour_match = re.search(r"(\d+)\s*hours?", hs_str)
        min_match = re.search(r"(\d+)\s*minutes?", hs_str)
        sec_match = re.search(r"(\d+)\s*seconds?", hs_str)

        if hour_match:
            total_seconds += int(hour_match.group(1)) * 3600
        if min_match:
            total_seconds += int(min_match.group(1)) * 60
        if sec_match:
            total_seconds += int(sec_match.group(1))

        return total_seconds if total_seconds > 0 else None


# =============================================================================
# Network Listeners Capability
# =============================================================================


class NetworkListenersCapability(BaseCapability[NetworkListenersInput, NetworkListenersOutput]):
    """List processes listening on network ports."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="network.listeners",
            summary="List processes listening on network ports",
            domain="network",
            risk="none",
            side_effects=(),
            requires_privilege=False,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="NetworkListenersInput",
            output_schema_name="NetworkListenersOutput",
        )

    def dry_run(self, input: NetworkListenersInput) -> DryRunResult:
        """Preview listener scan."""
        return DryRunResult(
            would_execute=("scan /proc/net/tcp*",),
            would_modify=(),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text="Would scan for listening network sockets",
            is_valid=True,
        )

    def run(self, input: NetworkListenersInput) -> CapabilityResult:
        """Scan for listening sockets."""
        start_time = time.time()

        try:
            listeners = self._scan_listeners(input)

            return CapabilityResult(
                success=True,
                output=NetworkListenersOutput(
                    listeners=tuple(listeners),
                    total=len(listeners),
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    rationale=f"Found {len(listeners)} listening sockets",
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

    def _scan_listeners(self, input: NetworkListenersInput) -> list[ListenerInfo]:
        """Scan /proc/net for listeners.

        Args:
            input: Filter configuration.

        Returns:
            List of ListenerInfo objects.
        """
        listeners: list[ListenerInfo] = []

        # Files to scan
        files = []
        if input.protocol is None or input.protocol == "tcp":
            files.extend(["/proc/net/tcp", "/proc/net/tcp6"])
        if input.protocol is None or input.protocol == "udp":
            files.extend(["/proc/net/udp", "/proc/net/udp6"])

        for proc_file in files:
            path = Path(proc_file)
            if not path.exists():
                continue

            protocol = "tcp" if "tcp" in proc_file else "udp"

            try:
                content = path.read_text()
                for line in content.strip().split("\n")[1:]:  # Skip header
                    parts = line.split()
                    if len(parts) < 10:
                        continue

                    # Parse local address:port
                    local = parts[1]
                    addr_hex, port_hex = local.split(":")

                    # Convert hex port
                    port = int(port_hex, 16)

                    # Filter by port if specified
                    if input.port is not None and port != input.port:
                        continue

                    # Parse address
                    address = self._hex_to_ip(addr_hex)

                    # Check if listening (state = 0A for TCP LISTEN)
                    state = parts[3]
                    if protocol == "tcp" and state != "0A":
                        continue

                    # Skip loopback if not requested
                    is_loopback = address in ("127.0.0.1", "::1")
                    if is_loopback and not input.include_loopback:
                        continue

                    # Check if wildcard
                    is_wildcard = address in ("0.0.0.0", "::")  # nosec B104

                    # Get PID and process name
                    inode = parts[9]
                    pid, proc_name = self._get_process_for_inode(inode)

                    listeners.append(
                        ListenerInfo(
                            port=port,
                            protocol=protocol,
                            address=address,
                            pid=pid,
                            process_name=proc_name,
                            is_wildcard=is_wildcard,
                        )
                    )

            except Exception as e:
                logger.warning(f"Failed to read {proc_file}: {e}")

        # Deduplicate by port/protocol/address
        seen = set()
        unique: list[ListenerInfo] = []
        for l in listeners:
            key = (l.port, l.protocol, l.address)
            if key not in seen:
                seen.add(key)
                unique.append(l)

        return unique

    def _hex_to_ip(self, hex_str: str) -> str:
        """Convert hex address to IP string.

        Args:
            hex_str: Hex IP address.

        Returns:
            IP address string.
        """
        if len(hex_str) == 8:
            # IPv4
            octets = [str(int(hex_str[i : i + 2], 16)) for i in range(0, 8, 2)]
            return ".".join(reversed(octets))
        else:
            # IPv6
            if hex_str == "00000000000000000000000000000000":
                return "::"
            if hex_str == "00000000000000000000000001000000":
                return "::1"
            return hex_str  # Return raw for complex IPv6

    def _get_process_for_inode(self, inode: str) -> tuple[int | None, str]:
        """Get PID and process name for a socket inode.

        Args:
            inode: Socket inode number.

        Returns:
            Tuple of (pid, process_name).
        """
        if inode == "0":
            return None, "kernel"

        try:
            # Scan /proc/*/fd for matching socket
            proc_path = Path("/proc")
            socket_link = f"socket:[{inode}]"

            for pid_dir in proc_path.iterdir():
                if not pid_dir.name.isdigit():
                    continue

                fd_dir = pid_dir / "fd"
                if not fd_dir.exists():
                    continue

                try:
                    for fd in fd_dir.iterdir():
                        try:
                            if fd.resolve().name == socket_link or str(fd.resolve()) == socket_link:
                                # Found the process
                                pid = int(pid_dir.name)

                                # Get process name
                                comm_file = pid_dir / "comm"
                                if comm_file.exists():
                                    proc_name = comm_file.read_text().strip()
                                else:
                                    proc_name = "unknown"

                                return pid, proc_name
                        except (OSError, PermissionError):
                            continue
                except (OSError, PermissionError):
                    continue

        except Exception:
            pass

        return None, "unknown"


# =============================================================================
# WireGuard Key Generation Capability
# =============================================================================


class WireGuardGenerateKeyInput(BaseModel):
    """Input for wireguard.generate-key operation."""

    model_config = ConfigDict(frozen=True)

    include_preshared_key: bool = Field(
        default=False,
        description="Also generate a preshared key",
    )


class WireGuardGenerateKeyOutput(BaseModel):
    """Output from wireguard.generate-key operation."""

    model_config = ConfigDict(frozen=True)

    private_key: str = Field(description="Generated private key (base64)")
    public_key: str = Field(description="Derived public key (base64)")
    preshared_key: str | None = Field(
        default=None,
        description="Generated preshared key if requested",
    )


class WireGuardGenerateKeyCapability(BaseCapability[WireGuardGenerateKeyInput, WireGuardGenerateKeyOutput]):
    """Generate WireGuard keypair."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="wireguard.generate-key",
            summary="Generate a new WireGuard keypair",
            domain="network",
            risk="low",
            side_effects=(),
            requires_privilege=False,
            idempotent=False,
            trust_level="core",
            version="1.0.0",
            input_schema_name="WireGuardGenerateKeyInput",
            output_schema_name="WireGuardGenerateKeyOutput",
        )

    def dry_run(self, input: WireGuardGenerateKeyInput) -> DryRunResult:
        """Preview key generation."""
        if not _is_wireguard_available():
            return DryRunResult(
                is_valid=False,
                validation_errors=("wg command not available",),
                preview_text="Cannot generate keys: wg not found",
            )

        return DryRunResult(
            would_execute=("wg genkey", "wg pubkey"),
            would_modify=(),
            estimated_risk="low",
            requires_confirmation=False,
            preview_text="Would generate a new WireGuard keypair",
            is_valid=True,
        )

    def run(self, input: WireGuardGenerateKeyInput) -> CapabilityResult:
        """Generate WireGuard keys."""
        start_time = time.time()

        try:
            from elle.ops.wireguard.keys import (
                generate_keypair,
                generate_preshared_key,
            )

            private_key, public_key = generate_keypair()

            preshared_key = None
            if input.include_preshared_key:
                preshared_key = generate_preshared_key()

            return CapabilityResult(
                success=True,
                output=WireGuardGenerateKeyOutput(
                    private_key=private_key,
                    public_key=public_key,
                    preshared_key=preshared_key,
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    commands_executed=("wg genkey", "wg pubkey"),
                    rationale="Generated new WireGuard keypair",
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )


# =============================================================================
# WireGuard Key Rotation Capability
# =============================================================================


class WireGuardRotateKeysInput(BaseModel):
    """Input for wireguard.rotate-keys operation."""

    model_config = ConfigDict(frozen=True)

    interface: str = Field(
        default="wg0",
        description="WireGuard interface name",
        pattern=r"^wg\d+$",
    )
    grace_period_seconds: int = Field(
        default=300,
        ge=0,
        le=3600,
        description="Suggested time before removing old key from peers",
    )


class WireGuardRotateKeysOutput(BaseModel):
    """Output from wireguard.rotate-keys operation."""

    model_config = ConfigDict(frozen=True)

    interface: str = Field(description="Interface that was rotated")
    old_public_key: str | None = Field(
        default=None,
        description="Previous public key",
    )
    new_public_key: str | None = Field(
        default=None,
        description="New public key",
    )
    backup_path: str | None = Field(
        default=None,
        description="Path to config backup",
    )
    steps_completed: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Steps that were completed",
    )


class WireGuardRotateKeysCapability(BaseCapability[WireGuardRotateKeysInput, WireGuardRotateKeysOutput]):
    """Rotate WireGuard keys for an interface."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="wireguard.rotate-keys",
            summary="Rotate WireGuard keys without dropping connections",
            domain="network",
            risk="high",
            side_effects=(
                SideEffect(
                    kind="network_change",
                    target="{interface}",
                    reversible=True,
                    description="Interface keys will be rotated",
                ),
                SideEffect(
                    kind="privilege_escalation",
                    target="/etc/wireguard",
                    reversible=False,
                    description="Requires root to modify WireGuard config",
                ),
            ),
            requires_privilege=True,
            idempotent=False,
            trust_level="core",
            version="1.0.0",
            input_schema_name="WireGuardRotateKeysInput",
            output_schema_name="WireGuardRotateKeysOutput",
        )

    def dry_run(self, input: WireGuardRotateKeysInput) -> DryRunResult:
        """Preview key rotation."""
        if not _is_wireguard_available():
            return DryRunResult(
                is_valid=False,
                validation_errors=("wg command not available",),
                preview_text="Cannot rotate keys: wg not found",
            )

        config_path = Path(f"/etc/wireguard/{input.interface}.conf")
        if not config_path.exists():
            return DryRunResult(
                is_valid=False,
                validation_errors=(f"Config not found: {config_path}",),
                preview_text=f"Cannot rotate: no config for {input.interface}",
            )

        is_up = _interface_exists(input.interface)

        return DryRunResult(
            would_execute=(
                "wg genkey",
                "wg pubkey",
                f"backup /etc/wireguard/{input.interface}.conf",
                f"update {input.interface}.conf with new key",
                f"wg-quick down {input.interface}",
                f"wg-quick up {input.interface}",
            ),
            would_modify=(f"/etc/wireguard/{input.interface}.conf",),
            estimated_risk="high",
            requires_confirmation=True,
            preview_text=(
                f"Would rotate keys for '{input.interface}' "
                f"({'currently up' if is_up else 'currently down'}). "
                f"Peers must be updated with new public key within "
                f"{input.grace_period_seconds}s."
            ),
            is_valid=True,
        )

    def run(self, input: WireGuardRotateKeysInput) -> CapabilityResult:
        """Rotate WireGuard keys."""
        start_time = time.time()

        try:
            from elle.ops.wireguard.keys import rotate_keys_safely

            result = rotate_keys_safely(
                input.interface,
                grace_period_seconds=input.grace_period_seconds,
            )

            if result.success:
                return CapabilityResult(
                    success=True,
                    output=WireGuardRotateKeysOutput(
                        interface=result.interface,
                        old_public_key=result.old_public_key,
                        new_public_key=result.new_public_key,
                        backup_path=result.backup_path,
                        steps_completed=result.steps_completed,
                    ),
                    side_effects_applied=(
                        SideEffect(
                            kind="network_change",
                            target=input.interface,
                            reversible=True,
                            description=(
                                f"Rotated keys for {input.interface}: "
                                f"{result.old_public_key[:8] if result.old_public_key else '?'}... -> "
                                f"{result.new_public_key[:8] if result.new_public_key else '?'}..."
                            ),
                        ),
                    ),
                    warnings=(f"Peers must be updated with new public key: {result.new_public_key}",),
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    evidence=CapabilityEvidence(
                        commands_executed=result.steps_completed,
                        rationale=f"Rotated keys for {input.interface}",
                    ),
                )
            else:
                return CapabilityResult(
                    success=False,
                    error=result.error,
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    evidence=CapabilityEvidence(
                        commands_executed=result.steps_completed,
                    ),
                )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

    def rollback(
        self,
        input: WireGuardRotateKeysInput,
        result: CapabilityResult,
    ) -> RollbackResult:
        """Rollback by restoring backup config."""
        if not result.output or not result.output.backup_path:
            return RollbackResult(
                success=False,
                error="No backup path to restore from",
            )

        backup_path = Path(result.output.backup_path)
        config_path = Path(f"/etc/wireguard/{input.interface}.conf")

        if not backup_path.exists():
            return RollbackResult(
                success=False,
                error=f"Backup not found: {backup_path}",
            )

        try:
            import shutil

            shutil.copy2(backup_path, config_path)

            # Restart interface
            _run_command(["sudo", "wg-quick", "down", input.interface])
            _run_command(["sudo", "wg-quick", "up", input.interface])

            return RollbackResult(
                success=True,
                rolled_back=(str(config_path),),
                commands_executed=(
                    f"restored {config_path} from {backup_path}",
                    f"wg-quick down {input.interface}",
                    f"wg-quick up {input.interface}",
                ),
            )

        except Exception as e:
            return RollbackResult(
                success=False,
                error=str(e),
                failed_to_rollback=(str(config_path),),
            )


# =============================================================================
# Network Diagnose Capability
# =============================================================================


class NetworkDiagnoseInput(BaseModel):
    """Input for network.diagnose operation."""

    model_config = ConfigDict(frozen=True)

    target: str = Field(description="Target hostname or IP address")
    port: int | None = Field(
        default=None,
        description="Target port (optional but recommended for TCP services)",
    )


class NetworkDiagnoseOutput(BaseModel):
    """Output from network.diagnose operation."""

    model_config = ConfigDict(frozen=True)

    target: str = Field(description="Target that was diagnosed")
    port: int | None = Field(default=None, description="Port tested")
    reachable: bool = Field(description="Whether target is reachable")
    likely_cause: str | None = Field(
        default=None,
        description="Most likely cause if unreachable",
    )
    summary: str = Field(description="Natural language summary")
    checks_passed: int = Field(description="Number of checks that passed")
    checks_failed: int = Field(description="Number of checks that failed")
    suggestions: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Suggested fixes",
    )


class NetworkDiagnoseCapability(BaseCapability[NetworkDiagnoseInput, NetworkDiagnoseOutput]):
    """Diagnose network connectivity issues with root cause analysis."""

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="network.diagnose",
            summary="Diagnose network connectivity with systematic checks",
            domain="network",
            risk="none",
            side_effects=(),  # Read-only operation
            requires_privilege=False,
            idempotent=True,
            trust_level="core",
            version="1.0.0",
            input_schema_name="NetworkDiagnoseInput",
            output_schema_name="NetworkDiagnoseOutput",
        )

    def dry_run(self, input: NetworkDiagnoseInput) -> DryRunResult:
        """Preview network diagnosis."""
        checks = [
            "DNS resolution check",
            "Route verification",
            "Firewall inspection",
        ]
        if input.port:
            checks.append(f"TCP connection to port {input.port}")
        else:
            checks.append("ICMP ping test")

        return DryRunResult(
            would_execute=tuple(checks),
            would_modify=(),
            estimated_risk="none",
            requires_confirmation=False,
            preview_text=f"Would diagnose connectivity to {input.target}" + (f":{input.port}" if input.port else ""),
            is_valid=True,
        )

    def run(self, input: NetworkDiagnoseInput) -> CapabilityResult:
        """Run network diagnosis."""
        start_time = time.time()

        try:
            from elle.cli.network.diagnosis import diagnose_connectivity

            diagnosis = diagnose_connectivity(input.target, port=input.port)

            checks_passed = sum(1 for c in diagnosis.checks if c.passed)
            checks_failed = sum(1 for c in diagnosis.checks if not c.passed)

            return CapabilityResult(
                success=True,
                output=NetworkDiagnoseOutput(
                    target=diagnosis.target,
                    port=diagnosis.port,
                    reachable=diagnosis.reachable,
                    likely_cause=diagnosis.likely_cause,
                    summary=diagnosis.summary,
                    checks_passed=checks_passed,
                    checks_failed=checks_failed,
                    suggestions=diagnosis.suggestions,
                ),
                execution_time_ms=int((time.time() - start_time) * 1000),
                evidence=CapabilityEvidence(
                    commands_executed=(
                        "DNS resolution",
                        "ip route get",
                        "firewall check",
                        f"TCP connect to {input.target}:{input.port}" if input.port else f"ping {input.target}",
                    ),
                    rationale=f"Diagnosed {input.target}: "
                    + ("reachable" if diagnosis.reachable else diagnosis.likely_cause or "unreachable"),
                ),
            )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=f"Network diagnosis failed: {e}",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )


# =============================================================================
# Exports
# =============================================================================

NETWORK_CAPABILITIES = [
    WireGuardRestartCapability,
    WireGuardStatusCapability,
    NetworkListenersCapability,
    WireGuardGenerateKeyCapability,
    WireGuardRotateKeysCapability,
    NetworkDiagnoseCapability,
]
