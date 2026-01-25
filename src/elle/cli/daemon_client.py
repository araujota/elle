"""Daemon client for CLI to query the elled API.

Provides typed access to the daemon's state APIs, replacing direct
subprocess calls in CLI components with daemon queries.

Authentication:
    The client automatically reads the session token from the token file
    written by elled on startup. This ensures only elle processes running
    as the same user can access the daemon API.

Usage:
    client = DaemonClient()
    docker_state = await client.get_docker_state()
    network_state = await client.get_network_state()
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


def _read_session_token() -> str | None:
    """Read the session token from the daemon's token file.

    Returns:
        The session token, or None if not available.
    """
    try:
        from elle.daemon.api.session_token import read_token_file
        return read_token_file()
    except ImportError:
        # Fallback: try reading directly from common locations
        import os

        token_paths = [
            Path(os.environ.get("XDG_RUNTIME_DIR", "")) / "elle" / "session.token",
            Path.home() / ".local" / "state" / "elle" / "session.token",
            Path("/tmp") / f"elle-{os.getuid()}" / "session.token",
        ]

        for path in token_paths:
            try:
                if path.exists():
                    return path.read_text().strip()
            except Exception:
                continue

        return None
    except Exception as e:
        logger.debug(f"Failed to read session token: {e}")
        return None


# ============================================================================
# Response Models (matching daemon/api/state_routes.py)
# ============================================================================


class ContainerInfo(BaseModel):
    """Container information."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Container ID (short)")
    names: str = Field(description="Container name")
    image: str = Field(description="Image name:tag")
    status: str = Field(description="Container status")
    state: str = Field(description="Running state")
    ports: str = Field(default="", description="Port mappings")


class DockerState(BaseModel):
    """Docker environment state from daemon."""

    model_config = ConfigDict(frozen=True)

    docker_available: bool = Field(default=False)
    running_containers: tuple[ContainerInfo, ...] = Field(default_factory=tuple)
    all_containers: tuple[ContainerInfo, ...] = Field(default_factory=tuple)
    images: tuple[str, ...] = Field(default_factory=tuple)
    networks: tuple[str, ...] = Field(default_factory=tuple)
    volumes: tuple[str, ...] = Field(default_factory=tuple)
    compose_services: tuple[str, ...] = Field(default_factory=tuple)
    swarm_active: bool = Field(default=False)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InterfaceInfo(BaseModel):
    """Network interface information."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Interface name")
    state: str = Field(description="Operational state")
    addresses: tuple[str, ...] = Field(default_factory=tuple)
    mac: str | None = Field(default=None)
    mtu: int | None = Field(default=None)


class FirewallRule(BaseModel):
    """Firewall rule."""

    model_config = ConfigDict(frozen=True)

    action: str = Field(description="ALLOW/DENY/REJECT")
    direction: str = Field(description="in/out")
    port: str | None = Field(default=None)
    proto: str = Field(default="any")
    from_addr: str = Field(default="any")
    to_addr: str = Field(default="any")


class FirewallState(BaseModel):
    """Firewall state from daemon."""

    model_config = ConfigDict(frozen=True)

    active: bool = Field(default=False)
    backend: str = Field(default="unknown")
    default_incoming: str = Field(default="allow")
    default_outgoing: str = Field(default="allow")
    rules: tuple[FirewallRule, ...] = Field(default_factory=tuple)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class NetworkState(BaseModel):
    """Network state from daemon."""

    model_config = ConfigDict(frozen=True)

    interfaces: tuple[InterfaceInfo, ...] = Field(default_factory=tuple)
    default_gateway: str | None = Field(default=None)
    default_interface: str | None = Field(default=None)
    dns_servers: tuple[str, ...] = Field(default_factory=tuple)
    hostname: str = Field(default="")
    wireguard_interfaces: tuple[str, ...] = Field(default_factory=tuple)
    firewall_active: bool = Field(default=False)
    firewall_backend: str = Field(default="unknown")
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Listener(BaseModel):
    """Network listener."""

    model_config = ConfigDict(frozen=True)

    port: int = Field(description="Port number")
    proto: str = Field(description="Protocol")
    address: str = Field(description="Bound address")
    pid: int | None = Field(default=None)
    process: str = Field(default="unknown")
    is_wildcard: bool = Field(default=False)


# ============================================================================
# Daemon Client
# ============================================================================


class DaemonClient:
    """Client for querying the elled daemon API.

    Replaces direct subprocess calls in CLI with daemon API queries.
    Falls back to local subprocess calls if the daemon is unavailable.

    Authentication is handled automatically by reading the session token
    from the daemon's token file.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8377",
        timeout: float = 10.0,
    ) -> None:
        """Initialize the daemon client.

        Args:
            base_url: Daemon API base URL.
            timeout: Request timeout in seconds.
        """
        self._base_url = base_url
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._daemon_available: bool | None = None
        self._session_token: str | None = None
        self._token_loaded: bool = False

    def _load_session_token(self) -> str | None:
        """Load the session token (lazy, cached).

        Returns:
            The session token, or None if not available.
        """
        if not self._token_loaded:
            self._session_token = _read_session_token()
            self._token_loaded = True
            if self._session_token:
                logger.debug("Session token loaded successfully")
            else:
                logger.debug("No session token available")
        return self._session_token

    def _get_headers(self) -> dict[str, str]:
        """Get headers including authentication.

        Returns:
            Headers dict with auth token if available.
        """
        headers: dict[str, str] = {}
        token = self._load_session_token()
        if token:
            headers["X-Elle-Token"] = token
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client with auth headers."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers=self._get_headers(),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client and reset state."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        # Reset token state so it's reloaded on next use
        self._token_loaded = False
        self._session_token = None
        self._daemon_available = None

    async def is_daemon_available(self) -> bool:
        """Check if the daemon is running and accessible.

        Returns:
            True if daemon is available.
        """
        try:
            client = await self._get_client()
            response = await client.get("/v1/health")
            self._daemon_available = response.status_code == 200
            return self._daemon_available
        except Exception:
            self._daemon_available = False
            return False

    async def get_docker_state(self) -> DockerState:
        """Get Docker environment state from daemon.

        Returns:
            DockerState with container/image info.
        """
        try:
            client = await self._get_client()
            response = await client.get("/v1/state/docker")

            if response.status_code == 200:
                data = response.json()
                return DockerState(
                    docker_available=data.get("docker_available", False),
                    running_containers=tuple(
                        ContainerInfo(**c) for c in data.get("running_containers", [])
                    ),
                    all_containers=tuple(
                        ContainerInfo(**c) for c in data.get("all_containers", [])
                    ),
                    images=tuple(data.get("images", [])),
                    networks=tuple(data.get("networks", [])),
                    volumes=tuple(data.get("volumes", [])),
                    compose_services=tuple(data.get("compose_services", [])),
                    swarm_active=data.get("swarm_active", False),
                    collected_at=datetime.fromisoformat(data.get("collected_at", datetime.now(UTC).isoformat())),
                )
            else:
                logger.warning(f"Daemon returned {response.status_code} for docker state")
                return await self._get_docker_state_fallback()

        except Exception as e:
            logger.debug(f"Failed to get docker state from daemon: {e}")
            return await self._get_docker_state_fallback()

    async def get_network_state(self) -> NetworkState:
        """Get network state from daemon.

        Returns:
            NetworkState with interface/firewall info.
        """
        try:
            client = await self._get_client()
            response = await client.get("/v1/state/network")

            if response.status_code == 200:
                data = response.json()
                return NetworkState(
                    interfaces=tuple(
                        InterfaceInfo(
                            name=i.get("name", ""),
                            state=i.get("state", ""),
                            addresses=tuple(i.get("addresses", [])),
                            mac=i.get("mac"),
                            mtu=i.get("mtu"),
                        )
                        for i in data.get("interfaces", [])
                    ),
                    default_gateway=data.get("default_gateway"),
                    default_interface=data.get("default_interface"),
                    dns_servers=tuple(data.get("dns_servers", [])),
                    hostname=data.get("hostname", ""),
                    wireguard_interfaces=tuple(data.get("wireguard_interfaces", [])),
                    firewall_active=data.get("firewall_active", False),
                    firewall_backend=data.get("firewall_backend", "unknown"),
                    collected_at=datetime.fromisoformat(data.get("collected_at", datetime.now(UTC).isoformat())),
                )
            else:
                logger.warning(f"Daemon returned {response.status_code} for network state")
                return await self._get_network_state_fallback()

        except Exception as e:
            logger.debug(f"Failed to get network state from daemon: {e}")
            return await self._get_network_state_fallback()

    async def get_firewall_state(self) -> FirewallState:
        """Get firewall state from daemon.

        Returns:
            FirewallState with rules and policies.
        """
        try:
            client = await self._get_client()
            response = await client.get("/v1/state/firewall")

            if response.status_code == 200:
                data = response.json()
                return FirewallState(
                    active=data.get("active", False),
                    backend=data.get("backend", "unknown"),
                    default_incoming=data.get("default_incoming", "allow"),
                    default_outgoing=data.get("default_outgoing", "allow"),
                    rules=tuple(
                        FirewallRule(**r) for r in data.get("rules", [])
                    ),
                    collected_at=datetime.fromisoformat(data.get("collected_at", datetime.now(UTC).isoformat())),
                )
            else:
                return FirewallState()

        except Exception as e:
            logger.debug(f"Failed to get firewall state from daemon: {e}")
            return FirewallState()

    async def get_listeners(self) -> tuple[Listener, ...]:
        """Get network listeners from daemon.

        Returns:
            Tuple of Listener objects.
        """
        try:
            client = await self._get_client()
            response = await client.get("/v1/state/listeners")

            if response.status_code == 200:
                data = response.json()
                return tuple(
                    Listener(**l) for l in data.get("listeners", [])
                )
            else:
                return ()

        except Exception as e:
            logger.debug(f"Failed to get listeners from daemon: {e}")
            return ()

    async def refresh_state(self) -> bool:
        """Force the daemon to refresh all cached state.

        Returns:
            True if refresh succeeded.
        """
        try:
            client = await self._get_client()
            response = await client.post("/v1/state/refresh")
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Failed to refresh daemon state: {e}")
            return False

    # ========================================================================
    # Vault Methods (M1-M4)
    # ========================================================================

    async def trigger_manvault_reindex(self) -> tuple[bool, str]:
        """Trigger a Man Vault reindex via the daemon.

        Returns:
            Tuple of (success, message).
        """
        try:
            client = await self._get_client()
            response = await client.post("/v1/manvault/reindex")

            if response.status_code == 200:
                data = response.json()
                return True, data.get("message", "Reindex started")
            else:
                return False, f"Daemon returned {response.status_code}"

        except Exception as e:
            logger.debug(f"Failed to trigger reindex via daemon: {e}")
            return False, str(e)

    async def get_manvault_status(self) -> dict[str, Any]:
        """Get Man Vault status from daemon.

        Returns:
            Status dict with indexing info.
        """
        try:
            client = await self._get_client()
            response = await client.get("/v1/manvault/status")

            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"Daemon returned {response.status_code}"}

        except Exception as e:
            logger.debug(f"Failed to get manvault status: {e}")
            return {"error": str(e)}

    async def search_manvault(
        self,
        query: str,
        limit: int = 10,
        use_semantic: bool = True,
    ) -> list[dict[str, Any]]:
        """Search Man Vault via the daemon.

        Args:
            query: Search query.
            limit: Max results.
            use_semantic: Enable semantic search.

        Returns:
            List of search results.
        """
        try:
            client = await self._get_client()
            response = await client.get(
                "/v1/manvault/search",
                params={"q": query, "limit": limit, "use_semantic": use_semantic},
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("results", [])
            else:
                logger.warning(f"Daemon returned {response.status_code} for manvault search")
                return []

        except Exception as e:
            logger.debug(f"Failed to search manvault via daemon: {e}")
            return []

    async def search_incidents(
        self,
        query: str,
        limit: int = 10,
        domain: str | None = None,
        use_semantic: bool = True,
    ) -> list[dict[str, Any]]:
        """Search Incident Vault via the daemon.

        Args:
            query: Search query (error message).
            limit: Max results.
            domain: Optional domain filter.
            use_semantic: Enable semantic search.

        Returns:
            List of matching incidents.
        """
        try:
            client = await self._get_client()
            params: dict[str, Any] = {
                "q": query,
                "limit": limit,
                "use_semantic": use_semantic,
            }
            if domain:
                params["domain"] = domain

            response = await client.get("/v1/incidents/search", params=params)

            if response.status_code == 200:
                data = response.json()
                return data.get("results", [])
            else:
                logger.warning(f"Daemon returned {response.status_code} for incident search")
                return []

        except Exception as e:
            logger.debug(f"Failed to search incidents via daemon: {e}")
            return []

    # ========================================================================
    # Fallback Methods (when daemon unavailable)
    # ========================================================================

    async def _get_docker_state_fallback(self) -> DockerState:
        """Fallback: Get Docker state via direct subprocess calls.

        This is the same logic that was in PlannerService._get_docker_state()
        but should only be used when the daemon is unavailable.
        """
        import json
        import subprocess

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
                return DockerState(docker_available=False)

            # Get running containers
            result = await asyncio.to_thread(
                subprocess.run,
                ["docker", "ps", "--format", "{{json .}}"],
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
                            containers.append(ContainerInfo(
                                id=data.get("ID", "")[:12],
                                names=data.get("Names", ""),
                                image=data.get("Image", ""),
                                status=data.get("Status", ""),
                                state=data.get("State", ""),
                                ports=data.get("Ports", ""),
                            ))
                        except json.JSONDecodeError:
                            pass

            # Get images
            result = await asyncio.to_thread(
                subprocess.run,
                ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            images = []
            if result.returncode == 0:
                images = [i for i in result.stdout.strip().split("\n") if i and i != "<none>:<none>"]

            # Get networks
            result = await asyncio.to_thread(
                subprocess.run,
                ["docker", "network", "ls", "--format", "{{.Name}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            networks = []
            if result.returncode == 0:
                networks = [n for n in result.stdout.strip().split("\n") if n]

            # Get volumes
            result = await asyncio.to_thread(
                subprocess.run,
                ["docker", "volume", "ls", "--format", "{{.Name}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            volumes = []
            if result.returncode == 0:
                volumes = [v for v in result.stdout.strip().split("\n") if v]

            return DockerState(
                docker_available=True,
                running_containers=tuple(containers),
                images=tuple(images[:20]),
                networks=tuple(networks),
                volumes=tuple(volumes[:20]),
            )

        except FileNotFoundError:
            return DockerState(docker_available=False)
        except subprocess.TimeoutExpired:
            logger.warning("Docker command timed out (fallback)")
            return DockerState(docker_available=False)
        except Exception as e:
            logger.warning(f"Failed to get Docker state (fallback): {e}")
            return DockerState(docker_available=False)

    async def _get_network_state_fallback(self) -> NetworkState:
        """Fallback: Get network state via direct subprocess calls.

        This is the same logic that was in PlannerService._get_network_state()
        but should only be used when the daemon is unavailable.
        """
        import json
        import re
        import subprocess
        from pathlib import Path

        interfaces: list[InterfaceInfo] = []
        default_gateway = None
        default_interface = None
        dns_servers: list[str] = []
        hostname = ""
        wireguard_interfaces: list[str] = []
        firewall_active = False
        firewall_backend = "unknown"

        try:
            # Get interfaces
            result = await asyncio.to_thread(
                subprocess.run,
                ["ip", "-j", "addr"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                try:
                    ifaces = json.loads(result.stdout)
                    for iface in ifaces:
                        name = iface.get("ifname", "")
                        if name == "lo":
                            continue
                        interfaces.append(InterfaceInfo(
                            name=name,
                            state=iface.get("operstate", "unknown").upper(),
                            addresses=tuple(
                                a.get("local", "")
                                for a in iface.get("addr_info", [])
                                if a.get("local")
                            ),
                            mac=iface.get("address"),
                            mtu=iface.get("mtu"),
                        ))
                except json.JSONDecodeError:
                    pass

            # Get default gateway
            result = await asyncio.to_thread(
                subprocess.run,
                ["ip", "route", "show", "default"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout:
                gw_match = re.search(r"via\s+(\S+)", result.stdout)
                iface_match = re.search(r"dev\s+(\S+)", result.stdout)
                if gw_match:
                    default_gateway = gw_match.group(1)
                if iface_match:
                    default_interface = iface_match.group(1)

            # Get DNS servers
            try:
                content = Path("/etc/resolv.conf").read_text()
                for line in content.split("\n"):
                    if line.startswith("nameserver"):
                        parts = line.split()
                        if len(parts) >= 2:
                            dns_servers.append(parts[1])
            except FileNotFoundError:
                pass

            # Get hostname
            result = await asyncio.to_thread(
                subprocess.run,
                ["hostname"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                hostname = result.stdout.strip()

            # Check firewall (UFW)
            result = await asyncio.to_thread(
                subprocess.run,
                ["ufw", "status"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                firewall_backend = "ufw"
                firewall_active = "Status: active" in result.stdout
            else:
                # Try iptables
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["iptables", "-L", "-n"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    firewall_backend = "iptables"
                    firewall_active = "DROP" in result.stdout or "REJECT" in result.stdout

            # Get WireGuard interfaces
            result = await asyncio.to_thread(
                subprocess.run,
                ["wg", "show", "interfaces"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                wireguard_interfaces = [i for i in result.stdout.strip().split() if i]

        except FileNotFoundError:
            pass
        except subprocess.TimeoutExpired:
            logger.warning("Network command timed out (fallback)")
        except Exception as e:
            logger.warning(f"Failed to get network state (fallback): {e}")

        return NetworkState(
            interfaces=tuple(interfaces),
            default_gateway=default_gateway,
            default_interface=default_interface,
            dns_servers=tuple(dns_servers),
            hostname=hostname,
            wireguard_interfaces=tuple(wireguard_interfaces),
            firewall_active=firewall_active,
            firewall_backend=firewall_backend,
        )


# ============================================================================
# Singleton Instance
# ============================================================================


_client: DaemonClient | None = None


def get_daemon_client() -> DaemonClient:
    """Get the global daemon client instance.

    Returns:
        DaemonClient singleton.
    """
    global _client
    if _client is None:
        _client = DaemonClient()
    return _client
