"""API routes for system state queries.

Provides endpoints for querying cached Docker, Network, and Firewall state.
These routes enable CLI components to query the daemon for system state
instead of making direct subprocess calls.

Routes:
- GET /v1/state/docker - Docker environment state
- GET /v1/state/network - Network configuration state
- GET /v1/state/firewall - Firewall rules and policies
- GET /v1/state/listeners - Network port listeners
- GET /v1/state - Complete system state snapshot
- POST /v1/state/refresh - Force state refresh
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from elle.daemon.api.auth import AuthContext, get_auth_context

if TYPE_CHECKING:
    from elle.daemon.telemetry.state_cache import StateCache

# Router instance
router = APIRouter(prefix="/v1/state", tags=["state"])

# State cache reference set during app creation
_state_cache: StateCache | None = None


def set_state_cache(cache: StateCache) -> None:
    """Set the state cache reference for routes.

    Args:
        cache: The StateCache instance.
    """
    global _state_cache
    _state_cache = cache


def get_state_cache() -> StateCache:
    """Get the state cache reference.

    Raises:
        HTTPException: If state cache not set.

    Returns:
        The StateCache instance.
    """
    if _state_cache is None:
        raise HTTPException(status_code=503, detail="State cache not initialized")
    return _state_cache


# ============================================================================
# Response Models
# ============================================================================


class ContainerResponse(BaseModel):
    """Container information in API response."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Container ID (short)")
    names: str = Field(description="Container name")
    image: str = Field(description="Image name:tag")
    status: str = Field(description="Container status")
    state: str = Field(description="Running state")
    ports: str = Field(default="", description="Port mappings")


class DockerStateResponse(BaseModel):
    """Response for GET /v1/state/docker."""

    model_config = ConfigDict(frozen=True)

    docker_available: bool = Field(description="Is Docker daemon available")
    running_containers: list[ContainerResponse] = Field(
        default_factory=list, description="Currently running containers"
    )
    all_containers: list[ContainerResponse] = Field(default_factory=list, description="All containers")
    images: list[str] = Field(default_factory=list, description="Available images")
    networks: list[str] = Field(default_factory=list, description="Docker networks")
    volumes: list[str] = Field(default_factory=list, description="Docker volumes")
    compose_services: list[str] = Field(default_factory=list, description="Compose-managed services")
    swarm_active: bool = Field(default=False, description="Is swarm mode active")
    collected_at: datetime = Field(description="When state was collected")


class InterfaceResponse(BaseModel):
    """Network interface in API response."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Interface name")
    state: str = Field(description="Operational state")
    addresses: list[str] = Field(default_factory=list, description="IP addresses")
    mac: str | None = Field(default=None, description="MAC address")
    mtu: int | None = Field(default=None, description="MTU size")


class FirewallRuleResponse(BaseModel):
    """Firewall rule in API response."""

    model_config = ConfigDict(frozen=True)

    action: str = Field(description="ALLOW/DENY/REJECT")
    direction: str = Field(description="in/out")
    port: str | None = Field(default=None, description="Port or range")
    proto: str = Field(default="any", description="Protocol")
    from_addr: str = Field(default="any", description="Source address")
    to_addr: str = Field(default="any", description="Destination address")


class FirewallStateResponse(BaseModel):
    """Response for GET /v1/state/firewall."""

    model_config = ConfigDict(frozen=True)

    active: bool = Field(description="Is firewall enabled")
    backend: str = Field(description="Backend (ufw/iptables/nftables)")
    default_incoming: str = Field(description="Default incoming policy")
    default_outgoing: str = Field(description="Default outgoing policy")
    rules: list[FirewallRuleResponse] = Field(default_factory=list, description="Firewall rules")
    collected_at: datetime = Field(description="When state was collected")


class NetworkStateResponse(BaseModel):
    """Response for GET /v1/state/network."""

    model_config = ConfigDict(frozen=True)

    interfaces: list[InterfaceResponse] = Field(default_factory=list, description="Network interfaces")
    default_gateway: str | None = Field(default=None, description="Default gateway IP")
    default_interface: str | None = Field(default=None, description="Default interface name")
    dns_servers: list[str] = Field(default_factory=list, description="DNS servers")
    hostname: str = Field(default="", description="System hostname")
    wireguard_interfaces: list[str] = Field(default_factory=list, description="WireGuard interfaces")
    firewall_active: bool = Field(default=False, description="Is firewall active")
    firewall_backend: str = Field(default="unknown", description="Firewall backend")
    collected_at: datetime = Field(description="When state was collected")


class ListenerResponse(BaseModel):
    """Network listener in API response."""

    model_config = ConfigDict(frozen=True)

    port: int = Field(description="Port number")
    proto: str = Field(description="Protocol (tcp/udp)")
    address: str = Field(description="Bound address")
    pid: int | None = Field(default=None, description="Process ID")
    process: str = Field(default="unknown", description="Process name")
    is_wildcard: bool = Field(default=False, description="Bound to 0.0.0.0 or ::")


class ListenersResponse(BaseModel):
    """Response for GET /v1/state/listeners."""

    model_config = ConfigDict(frozen=True)

    listeners: list[ListenerResponse] = Field(default_factory=list, description="Network listeners")
    total: int = Field(ge=0, description="Total listener count")
    collected_at: datetime = Field(description="When state was collected")


class SystemStateResponse(BaseModel):
    """Response for GET /v1/state."""

    model_config = ConfigDict(frozen=True)

    docker: DockerStateResponse = Field(description="Docker state")
    network: NetworkStateResponse = Field(description="Network state")
    listeners: list[ListenerResponse] = Field(default_factory=list, description="Network listeners")
    collected_at: datetime = Field(description="When state was collected")


class RefreshResponse(BaseModel):
    """Response for POST /v1/state/refresh."""

    model_config = ConfigDict(frozen=True)

    success: bool = Field(description="Whether refresh succeeded")
    message: str = Field(description="Status message")


class ErrorResponse(BaseModel):
    """Error response."""

    model_config = ConfigDict(frozen=True)

    error: str = Field(description="Error message")
    detail: str | None = Field(default=None, description="Detailed error info")


# ============================================================================
# Route Handlers
# ============================================================================


@router.get(
    "/docker",
    response_model=DockerStateResponse,
    responses={
        401: {"description": "Authentication required"},
        503: {"model": ErrorResponse},
    },
)
async def get_docker_state(
    auth: AuthContext = Depends(get_auth_context),
) -> DockerStateResponse:
    """Get cached Docker environment state. Requires authentication."""
    _ = auth  # Auth enforced by dependency
    cache = get_state_cache()
    state = cache.get_docker_state()

    return DockerStateResponse(
        docker_available=state.docker_available,
        running_containers=[
            ContainerResponse(
                id=c.id,
                names=c.names,
                image=c.image,
                status=c.status,
                state=c.state,
                ports=c.ports,
            )
            for c in state.running_containers
        ],
        all_containers=[
            ContainerResponse(
                id=c.id,
                names=c.names,
                image=c.image,
                status=c.status,
                state=c.state,
                ports=c.ports,
            )
            for c in state.all_containers
        ],
        images=list(state.images),
        networks=list(state.networks),
        volumes=list(state.volumes),
        compose_services=list(state.compose_services),
        swarm_active=state.swarm_active,
        collected_at=state.collected_at,
    )


@router.get(
    "/network",
    response_model=NetworkStateResponse,
    responses={
        401: {"description": "Authentication required"},
        503: {"model": ErrorResponse},
    },
)
async def get_network_state(
    auth: AuthContext = Depends(get_auth_context),
) -> NetworkStateResponse:
    """Get cached network configuration state. Requires authentication."""
    _ = auth
    cache = get_state_cache()
    state = cache.get_network_state()

    return NetworkStateResponse(
        interfaces=[
            InterfaceResponse(
                name=i.name,
                state=i.state,
                addresses=list(i.addresses),
                mac=i.mac,
                mtu=i.mtu,
            )
            for i in state.interfaces
        ],
        default_gateway=state.default_gateway,
        default_interface=state.default_interface,
        dns_servers=list(state.dns_servers),
        hostname=state.hostname,
        wireguard_interfaces=list(state.wireguard_interfaces),
        firewall_active=state.firewall.active,
        firewall_backend=state.firewall.backend,
        collected_at=state.collected_at,
    )


@router.get(
    "/firewall",
    response_model=FirewallStateResponse,
    responses={
        401: {"description": "Authentication required"},
        503: {"model": ErrorResponse},
    },
)
async def get_firewall_state(
    auth: AuthContext = Depends(get_auth_context),
) -> FirewallStateResponse:
    """Get cached firewall state. Requires authentication."""
    _ = auth
    cache = get_state_cache()
    state = cache.get_firewall_state()

    return FirewallStateResponse(
        active=state.active,
        backend=state.backend,
        default_incoming=state.default_incoming,
        default_outgoing=state.default_outgoing,
        rules=[
            FirewallRuleResponse(
                action=r.action,
                direction=r.direction,
                port=r.port,
                proto=r.proto,
                from_addr=r.from_addr,
                to_addr=r.to_addr,
            )
            for r in state.rules
        ],
        collected_at=state.collected_at,
    )


@router.get(
    "/listeners",
    response_model=ListenersResponse,
    responses={
        401: {"description": "Authentication required"},
        503: {"model": ErrorResponse},
    },
)
async def get_listeners(
    auth: AuthContext = Depends(get_auth_context),
) -> ListenersResponse:
    """Get cached network port listeners. Requires authentication."""
    _ = auth
    cache = get_state_cache()
    listeners = cache.get_listeners()
    network_state = cache.get_network_state()

    return ListenersResponse(
        listeners=[
            ListenerResponse(
                port=l.port,
                proto=l.proto,
                address=l.address,
                pid=l.pid,
                process=l.process,
                is_wildcard=l.is_wildcard,
            )
            for l in listeners
        ],
        total=len(listeners),
        collected_at=network_state.collected_at,
    )


@router.get(
    "",
    response_model=SystemStateResponse,
    responses={
        401: {"description": "Authentication required"},
        503: {"model": ErrorResponse},
    },
)
async def get_system_state(
    auth: AuthContext = Depends(get_auth_context),
) -> SystemStateResponse:
    """Get complete cached system state. Requires authentication."""
    _ = auth
    cache = get_state_cache()
    state = cache.get_system_state()

    docker_resp = DockerStateResponse(
        docker_available=state.docker.docker_available,
        running_containers=[
            ContainerResponse(
                id=c.id,
                names=c.names,
                image=c.image,
                status=c.status,
                state=c.state,
                ports=c.ports,
            )
            for c in state.docker.running_containers
        ],
        all_containers=[
            ContainerResponse(
                id=c.id,
                names=c.names,
                image=c.image,
                status=c.status,
                state=c.state,
                ports=c.ports,
            )
            for c in state.docker.all_containers
        ],
        images=list(state.docker.images),
        networks=list(state.docker.networks),
        volumes=list(state.docker.volumes),
        compose_services=list(state.docker.compose_services),
        swarm_active=state.docker.swarm_active,
        collected_at=state.docker.collected_at,
    )

    network_resp = NetworkStateResponse(
        interfaces=[
            InterfaceResponse(
                name=i.name,
                state=i.state,
                addresses=list(i.addresses),
                mac=i.mac,
                mtu=i.mtu,
            )
            for i in state.network.interfaces
        ],
        default_gateway=state.network.default_gateway,
        default_interface=state.network.default_interface,
        dns_servers=list(state.network.dns_servers),
        hostname=state.network.hostname,
        wireguard_interfaces=list(state.network.wireguard_interfaces),
        firewall_active=state.network.firewall.active,
        firewall_backend=state.network.firewall.backend,
        collected_at=state.network.collected_at,
    )

    return SystemStateResponse(
        docker=docker_resp,
        network=network_resp,
        listeners=[
            ListenerResponse(
                port=l.port,
                proto=l.proto,
                address=l.address,
                pid=l.pid,
                process=l.process,
                is_wildcard=l.is_wildcard,
            )
            for l in state.listeners
        ],
        collected_at=state.collected_at,
    )


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    responses={
        401: {"description": "Authentication required"},
        503: {"model": ErrorResponse},
    },
)
async def refresh_state(
    auth: AuthContext = Depends(get_auth_context),
) -> RefreshResponse:
    """Force an immediate refresh of all cached state. Requires authentication."""
    _ = auth
    cache = get_state_cache()

    try:
        await cache.refresh_now()
        return RefreshResponse(
            success=True,
            message="State refresh completed",
        )
    except Exception as e:
        return RefreshResponse(
            success=False,
            message=f"Refresh failed: {e}",
        )
