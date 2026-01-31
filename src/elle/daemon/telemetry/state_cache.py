"""State cache for system state (Docker, Network, Firewall).

Provides cached access to system state that was previously polled
directly by CLI modules via subprocess calls. This centralizes state
observation in the daemon, following the daemon primacy principle.

The state is periodically refreshed and can be queried via the daemon API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# ============================================================================
# State Models
# ============================================================================


class ContainerInfo(BaseModel):
    """Information about a running Docker container."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Container ID (short)")
    names: str = Field(description="Container name")
    image: str = Field(description="Image name:tag")
    status: str = Field(description="Container status")
    state: str = Field(description="Running state (running, exited, etc.)")
    ports: str = Field(default="", description="Port mappings")
    created: str = Field(default="", description="Creation time")


class DockerState(BaseModel):
    """Cached Docker environment state."""

    model_config = ConfigDict(frozen=True)

    docker_available: bool = Field(default=False, description="Is Docker daemon available")
    running_containers: tuple[ContainerInfo, ...] = Field(
        default_factory=tuple, description="Currently running containers"
    )
    all_containers: tuple[ContainerInfo, ...] = Field(
        default_factory=tuple, description="All containers (including stopped)"
    )
    images: tuple[str, ...] = Field(default_factory=tuple, description="Available images (name:tag)")
    networks: tuple[str, ...] = Field(default_factory=tuple, description="Docker networks")
    volumes: tuple[str, ...] = Field(default_factory=tuple, description="Docker volumes")
    compose_services: tuple[str, ...] = Field(default_factory=tuple, description="Compose-managed services")
    swarm_active: bool = Field(default=False, description="Is swarm mode active")
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this state was collected",
    )


class InterfaceInfo(BaseModel):
    """Information about a network interface."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Interface name (e.g., eth0)")
    state: str = Field(description="Operational state (UP/DOWN)")
    addresses: tuple[str, ...] = Field(default_factory=tuple, description="IP addresses")
    mac: str | None = Field(default=None, description="MAC address")
    mtu: int | None = Field(default=None, description="MTU size")


class FirewallRule(BaseModel):
    """A firewall rule."""

    model_config = ConfigDict(frozen=True)

    action: str = Field(description="ALLOW/DENY/REJECT")
    direction: str = Field(description="in/out")
    port: str | None = Field(default=None, description="Port or port range")
    proto: str = Field(default="any", description="Protocol (tcp/udp/any)")
    from_addr: str = Field(default="any", description="Source address")
    to_addr: str = Field(default="any", description="Destination address")
    comment: str | None = Field(default=None, description="Rule comment")


class FirewallState(BaseModel):
    """Cached firewall state."""

    model_config = ConfigDict(frozen=True)

    active: bool = Field(default=False, description="Is firewall enabled")
    backend: str = Field(default="unknown", description="Backend (ufw/iptables/nftables)")
    default_incoming: str = Field(default="allow", description="Default incoming policy")
    default_outgoing: str = Field(default="allow", description="Default outgoing policy")
    rules: tuple[FirewallRule, ...] = Field(default_factory=tuple, description="Firewall rules")
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this state was collected",
    )


class NetworkState(BaseModel):
    """Cached network state."""

    model_config = ConfigDict(frozen=True)

    interfaces: tuple[InterfaceInfo, ...] = Field(default_factory=tuple, description="Network interfaces")
    default_gateway: str | None = Field(default=None, description="Default gateway IP")
    default_interface: str | None = Field(default=None, description="Default interface name")
    dns_servers: tuple[str, ...] = Field(default_factory=tuple, description="DNS servers")
    hostname: str = Field(default="", description="System hostname")
    wireguard_interfaces: tuple[str, ...] = Field(default_factory=tuple, description="WireGuard interface names")
    firewall: FirewallState = Field(default_factory=FirewallState, description="Firewall state")
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this state was collected",
    )


class Listener(BaseModel):
    """A listening network socket."""

    model_config = ConfigDict(frozen=True)

    port: int = Field(description="Port number")
    proto: str = Field(description="Protocol (tcp/udp)")
    address: str = Field(description="Bound address")
    pid: int | None = Field(default=None, description="Process ID")
    process: str = Field(default="unknown", description="Process name")
    is_wildcard: bool = Field(default=False, description="Bound to 0.0.0.0 or ::")


class SystemState(BaseModel):
    """Complete cached system state."""

    model_config = ConfigDict(frozen=True)

    docker: DockerState = Field(default_factory=DockerState)
    network: NetworkState = Field(default_factory=NetworkState)
    listeners: tuple[Listener, ...] = Field(default_factory=tuple, description="Network listeners")
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this state was collected",
    )


# ============================================================================
# State Cache Implementation
# ============================================================================


class StateCache:
    """Daemon-side cache for system state.

    Periodically refreshes Docker, Network, and Firewall state so that
    CLI components can query cached state instead of running subprocess
    commands directly.

    Usage:
        cache = StateCache()
        await cache.start()  # Begins background refresh loop
        state = cache.get_docker_state()  # Returns cached state
        await cache.stop()
    """

    def __init__(
        self,
        docker_refresh_sec: int = 30,
        network_refresh_sec: int = 60,
        firewall_refresh_sec: int = 60,
    ) -> None:
        """Initialize the state cache.

        Args:
            docker_refresh_sec: Seconds between Docker state refreshes.
            network_refresh_sec: Seconds between network state refreshes.
            firewall_refresh_sec: Seconds between firewall state refreshes.
        """
        self._docker_refresh = docker_refresh_sec
        self._network_refresh = network_refresh_sec
        self._firewall_refresh = firewall_refresh_sec

        self._running = False
        self._docker_state = DockerState()
        self._network_state = NetworkState()
        self._listeners: tuple[Listener, ...] = ()

        self._last_docker_refresh: datetime | None = None
        self._last_network_refresh: datetime | None = None

        self._stats = {
            "docker_refreshes": 0,
            "network_refreshes": 0,
            "errors": 0,
        }

    async def start(self) -> None:
        """Start the background refresh loop."""
        if self._running:
            logger.warning("StateCache already running")
            return

        self._running = True
        logger.info("Starting StateCache")

        # Initial refresh
        await self._refresh_all()

        # Start background tasks
        asyncio.create_task(self._docker_refresh_loop())
        asyncio.create_task(self._network_refresh_loop())

    async def stop(self) -> None:
        """Stop the state cache."""
        self._running = False
        logger.info("StateCache stopped")

    async def _refresh_all(self) -> None:
        """Refresh all state immediately."""
        await asyncio.gather(
            self._refresh_docker_state(),
            self._refresh_network_state(),
            return_exceptions=True,
        )

    async def _docker_refresh_loop(self) -> None:
        """Background loop for Docker state refresh."""
        while self._running:
            try:
                await asyncio.sleep(self._docker_refresh)
                if self._running:
                    await self._refresh_docker_state()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Docker refresh error: {e}")
                self._stats["errors"] += 1

    async def _network_refresh_loop(self) -> None:
        """Background loop for network state refresh."""
        while self._running:
            try:
                await asyncio.sleep(self._network_refresh)
                if self._running:
                    await self._refresh_network_state()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Network refresh error: {e}")
                self._stats["errors"] += 1

    async def _refresh_docker_state(self) -> None:
        """Refresh Docker state from the system."""
        self._stats["docker_refreshes"] += 1

        try:
            # Check if Docker is available
            result = await asyncio.to_thread(
                subprocess.run,
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            docker_available = result.returncode == 0

            if not docker_available:
                self._docker_state = DockerState(
                    docker_available=False,
                    collected_at=datetime.now(timezone.utc),
                )
                self._last_docker_refresh = datetime.now(timezone.utc)
                return

            # Get running containers
            running_containers = await self._get_containers(all_containers=False)
            all_containers = await self._get_containers(all_containers=True)

            # Get images
            images = await self._get_docker_items(
                ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                filter_fn=lambda x: x and x != "<none>:<none>",
            )

            # Get networks
            networks = await self._get_docker_items(["docker", "network", "ls", "--format", "{{.Name}}"])

            # Get volumes
            volumes = await self._get_docker_items(["docker", "volume", "ls", "--format", "{{.Name}}"])

            # Check for compose services
            compose_services = await self._get_compose_services()

            # Check swarm mode
            swarm_active = await self._check_swarm_active()

            self._docker_state = DockerState(
                docker_available=True,
                running_containers=running_containers,
                all_containers=all_containers,
                images=tuple(images[:50]),  # Limit to 50
                networks=tuple(networks),
                volumes=tuple(volumes[:50]),  # Limit to 50
                compose_services=tuple(compose_services),
                swarm_active=swarm_active,
                collected_at=datetime.now(timezone.utc),
            )
            self._last_docker_refresh = datetime.now(timezone.utc)

        except FileNotFoundError:
            self._docker_state = DockerState(
                docker_available=False,
                collected_at=datetime.now(timezone.utc),
            )
        except subprocess.TimeoutExpired:
            logger.warning("Docker state refresh timed out")
            self._stats["errors"] += 1
        except Exception as e:
            logger.warning(f"Failed to refresh Docker state: {e}")
            self._stats["errors"] += 1

    async def _get_containers(self, all_containers: bool = False) -> tuple[ContainerInfo, ...]:
        """Get container list from Docker."""
        cmd = ["docker", "ps", "--format", "{{json .}}"]
        if all_containers:
            cmd.insert(2, "-a")

        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )

        containers = []
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line:
                    try:
                        data = json.loads(line)
                        containers.append(
                            ContainerInfo(
                                id=data.get("ID", "")[:12],
                                names=data.get("Names", ""),
                                image=data.get("Image", ""),
                                status=data.get("Status", ""),
                                state=data.get("State", ""),
                                ports=data.get("Ports", ""),
                                created=data.get("CreatedAt", ""),
                            )
                        )
                    except json.JSONDecodeError:
                        pass

        return tuple(containers)

    async def _get_docker_items(
        self,
        cmd: list[str],
        filter_fn: Any | None = None,
    ) -> list[str]:
        """Get list output from a Docker command."""
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )

        items = []
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line and (filter_fn is None or filter_fn(line)):
                    items.append(line)

        return items

    async def _get_compose_services(self) -> list[str]:
        """Get Compose-managed service names."""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["docker", "compose", "ps", "--format", "{{.Service}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return [s for s in result.stdout.strip().split("\n") if s]
        except Exception:
            pass
        return []

    async def _check_swarm_active(self) -> bool:
        """Check if Docker Swarm is active."""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["docker", "info", "--format", "{{.Swarm.LocalNodeState}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0 and "active" in result.stdout.lower()
        except Exception:
            return False

    async def _refresh_network_state(self) -> None:
        """Refresh network state from the system."""
        self._stats["network_refreshes"] += 1

        try:
            # Get interfaces
            interfaces = await self._get_interfaces()

            # Get default gateway
            default_gateway, default_interface = await self._get_default_route()

            # Get DNS servers
            dns_servers = await self._get_dns_servers()

            # Get hostname
            hostname = await self._get_hostname()

            # Get WireGuard interfaces
            wireguard_interfaces = await self._get_wireguard_interfaces()

            # Get firewall state
            firewall = await self._get_firewall_state()

            # Get listeners
            self._listeners = await self._get_listeners()

            self._network_state = NetworkState(
                interfaces=interfaces,
                default_gateway=default_gateway,
                default_interface=default_interface,
                dns_servers=dns_servers,
                hostname=hostname,
                wireguard_interfaces=wireguard_interfaces,
                firewall=firewall,
                collected_at=datetime.now(timezone.utc),
            )
            self._last_network_refresh = datetime.now(timezone.utc)

        except Exception as e:
            logger.warning(f"Failed to refresh network state: {e}")
            self._stats["errors"] += 1

    async def _get_interfaces(self) -> tuple[InterfaceInfo, ...]:
        """Get network interface information."""
        interfaces = []

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["ip", "-j", "addr"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                ifaces = json.loads(result.stdout)
                for iface in ifaces:
                    name = iface.get("ifname", "")
                    if name in ("lo",):  # Skip loopback
                        continue

                    addresses = tuple(a.get("local", "") for a in iface.get("addr_info", []) if a.get("local"))

                    interfaces.append(
                        InterfaceInfo(
                            name=name,
                            state=iface.get("operstate", "unknown").upper(),
                            addresses=addresses,
                            mac=iface.get("address"),
                            mtu=iface.get("mtu"),
                        )
                    )

        except (json.JSONDecodeError, FileNotFoundError):
            pass

        return tuple(interfaces)

    async def _get_default_route(self) -> tuple[str | None, str | None]:
        """Get default gateway and interface."""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["ip", "route", "show", "default"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0 and result.stdout:
                # Parse: default via 192.168.1.1 dev eth0
                gateway_match = re.search(r"via\s+(\S+)", result.stdout)
                iface_match = re.search(r"dev\s+(\S+)", result.stdout)

                return (
                    gateway_match.group(1) if gateway_match else None,
                    iface_match.group(1) if iface_match else None,
                )

        except FileNotFoundError:
            pass

        return None, None

    async def _get_dns_servers(self) -> tuple[str, ...]:
        """Get DNS server addresses."""
        servers = []

        try:
            content = Path("/etc/resolv.conf").read_text()
            for line in content.split("\n"):
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        servers.append(parts[1])
        except FileNotFoundError:
            pass

        return tuple(servers)

    async def _get_hostname(self) -> str:
        """Get system hostname."""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["hostname"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except FileNotFoundError:
            return ""

    async def _get_wireguard_interfaces(self) -> tuple[str, ...]:
        """Get WireGuard interface names."""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["wg", "show", "interfaces"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return tuple(i for i in result.stdout.strip().split() if i)
        except FileNotFoundError:
            pass

        return ()

    async def _get_firewall_state(self) -> FirewallState:
        """Get firewall state (UFW or iptables)."""
        # Try UFW first
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["ufw", "status", "verbose"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                return self._parse_ufw_status(result.stdout)

        except FileNotFoundError:
            pass

        # Fall back to iptables
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["iptables", "-L", "-n"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                active = "DROP" in result.stdout or "REJECT" in result.stdout
                return FirewallState(
                    active=active,
                    backend="iptables",
                    collected_at=datetime.now(timezone.utc),
                )

        except FileNotFoundError:
            pass

        return FirewallState(collected_at=datetime.now(timezone.utc))

    def _parse_ufw_status(self, output: str) -> FirewallState:
        """Parse UFW status verbose output."""
        active = "Status: active" in output
        default_incoming = "allow"
        default_outgoing = "allow"
        rules = []

        for line in output.split("\n"):
            # Parse default policies
            if "Default:" in line:
                if "deny (incoming)" in line or "reject (incoming)" in line:
                    default_incoming = "deny"
                if "deny (outgoing)" in line or "reject (outgoing)" in line:
                    default_outgoing = "deny"

            # Parse rules (very simplified)
            if "ALLOW" in line or "DENY" in line or "REJECT" in line:
                parts = line.split()
                if len(parts) >= 3:
                    action = "allow" if "ALLOW" in parts else ("deny" if "DENY" in parts else "reject")
                    port = parts[0] if parts[0] != "Anywhere" else None
                    rules.append(
                        FirewallRule(
                            action=action,
                            direction="in",
                            port=port,
                        )
                    )

        return FirewallState(
            active=active,
            backend="ufw",
            default_incoming=default_incoming,
            default_outgoing=default_outgoing,
            rules=tuple(rules),
            collected_at=datetime.now(timezone.utc),
        )

    async def _get_listeners(self) -> tuple[Listener, ...]:
        """Get network listeners via ss command."""
        listeners = []

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["ss", "-tlnp"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                for line in result.stdout.split("\n")[1:]:  # Skip header
                    parts = line.split()
                    if len(parts) >= 5:
                        local = parts[3]
                        # Parse address:port
                        if "]:" in local:  # IPv6
                            addr, port_str = local.rsplit(":", 1)
                            addr = addr.strip("[]")
                        else:
                            addr, port_str = local.rsplit(":", 1)

                        port = int(port_str) if port_str.isdigit() else 0
                        is_wildcard = addr in ("0.0.0.0", "*", "::")  # nosec B104

                        # Extract process info
                        process = "unknown"
                        pid = None
                        if len(parts) >= 6:
                            proc_info = parts[5]
                            pid_match = re.search(r"pid=(\d+)", proc_info)
                            name_match = re.search(r'"([^"]+)"', proc_info)
                            if pid_match:
                                pid = int(pid_match.group(1))
                            if name_match:
                                process = name_match.group(1)

                        listeners.append(
                            Listener(
                                port=port,
                                proto="tcp",
                                address=addr,
                                pid=pid,
                                process=process,
                                is_wildcard=is_wildcard,
                            )
                        )

        except FileNotFoundError:
            pass

        return tuple(listeners)

    # ========================================================================
    # Public API
    # ========================================================================

    def get_docker_state(self) -> DockerState:
        """Get cached Docker state."""
        return self._docker_state

    def get_network_state(self) -> NetworkState:
        """Get cached network state."""
        return self._network_state

    def get_firewall_state(self) -> FirewallState:
        """Get cached firewall state."""
        return self._network_state.firewall

    def get_listeners(self) -> tuple[Listener, ...]:
        """Get cached network listeners."""
        return self._listeners

    def get_system_state(self) -> SystemState:
        """Get complete cached system state."""
        return SystemState(
            docker=self._docker_state,
            network=self._network_state,
            listeners=self._listeners,
            collected_at=datetime.now(timezone.utc),
        )

    async def refresh_now(self) -> None:
        """Force an immediate refresh of all state."""
        await self._refresh_all()

    @property
    def is_running(self) -> bool:
        """Check if cache is running."""
        return self._running

    @property
    def stats(self) -> dict[str, int]:
        """Get cache statistics."""
        return self._stats.copy()
