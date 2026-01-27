"""FastAPI application for ELLE Mobile Gateway.

This module creates the FastAPI application that serves as the
mobile gateway. It handles:
- Device pairing via QR code tokens
- Proxied API requests to internal ELLE API
- Device management (localhost only)
- Elevation management (localhost only)
"""

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from elle.common.pydantic_compat import safe_model_dump
from elle.mobile.audit import MobileAuditStore
from elle.mobile.auth import (
    AuthenticationError,
    MobileAuthContext,
    MobileAuthenticator,
)
from elle.mobile.config import MobileGatewayConfig, get_mobile_config
from elle.mobile.crypto import MobileCrypto
from elle.mobile.elevation import ElevationManager, parse_ttl
from elle.mobile.models import DeviceStatus, MobileRole
from elle.mobile.pairing import PairingError, PairingManager
from elle.mobile.proxy import ProxyError, RequestProxy
from elle.mobile.roles import RoleEnforcer
from elle.mobile.store import MobileStore

logger = logging.getLogger(__name__)


# ============================================================================
# Request/Response Models
# ============================================================================


class PairingRequest(BaseModel):
    """Request to complete device pairing."""

    token: str = Field(..., description="Pairing token from QR code")
    device_name: str = Field(..., description="Human-readable device name")


class PairingResponse(BaseModel):
    """Response with client certificate for pairing."""

    device_id: str
    client_cert_pem: str
    client_key_pem: str
    ca_cert_pem: str


class DeviceResponse(BaseModel):
    """Device information response."""

    device_id: str
    name: str
    role: str
    status: str
    paired_at: str | None
    last_seen_at: str | None


class ElevateRequest(BaseModel):
    """Request to elevate device privileges."""

    ttl: str = Field(default="10m", description="Elevation TTL (e.g., '10m', '1h')")


class ErrorResponse(BaseModel):
    """Error response."""

    error: str
    code: str


# ============================================================================
# Dependency Injection
# ============================================================================


class GatewayDependencies:
    """Container for gateway dependencies."""

    def __init__(self, config: MobileGatewayConfig | None = None):
        self.config = config or get_mobile_config()
        self.store = MobileStore(self.config)
        self.crypto = MobileCrypto(self.config)
        self.pairing = PairingManager(self.config, self.store, self.crypto)
        self.elevation = ElevationManager(self.config, self.store)
        self.audit = MobileAuditStore(self.config)
        self.proxy = RequestProxy(self.config)
        self.authenticator = MobileAuthenticator(self.config, self.store, self.crypto)
        self.role_enforcer = RoleEnforcer()


# ============================================================================
# Factory Function
# ============================================================================


def create_mobile_gateway(
    config: MobileGatewayConfig | None = None,
) -> FastAPI:
    """Create the Mobile Gateway FastAPI application.

    Args:
        config: Mobile gateway configuration.

    Returns:
        Configured FastAPI application.
    """
    config = config or get_mobile_config()
    deps = GatewayDependencies(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Application lifespan handler."""
        from elle.daemon.notifications.mobile_push import get_mobile_notifier

        # Startup
        logger.info("Mobile Gateway starting")
        deps.crypto.ensure_certificates()
        deps.audit.log_gateway_start(f"{config.bind_host}:{config.bind_port}")

        # Enable mobile push notifications
        notifier = get_mobile_notifier()
        notifier.set_gateway_available(True)

        yield

        # Shutdown
        logger.info("Mobile Gateway stopping")

        # Disable mobile push notifications
        notifier.set_gateway_available(False)

        await deps.proxy.close()
        deps.audit.log_gateway_stop(f"{config.bind_host}:{config.bind_port}")

    app = FastAPI(
        title="ELLE Mobile Gateway",
        description="Secure remote access for mobile devices",
        version="1.0.0",
        lifespan=lifespan,
    )

    # ========================================================================
    # Authentication Dependency
    # ========================================================================

    async def get_auth(request: Request) -> MobileAuthContext:
        """Extract and validate authentication from request."""
        # Get client certificate from TLS
        client_cert_pem = None

        # Try to get from transport (uvicorn with SSL)
        if hasattr(request, "scope"):
            transport = request.scope.get("transport")
            if transport:
                ssl_object = transport.get_extra_info("ssl_object")
                if ssl_object:
                    peer_cert = ssl_object.getpeercert(binary_form=True)
                    if peer_cert:
                        from cryptography import x509
                        from cryptography.hazmat.primitives import serialization

                        cert = x509.load_der_x509_certificate(peer_cert)
                        client_cert_pem = cert.public_bytes(serialization.Encoding.PEM)

        # Get client IP
        client_ip = "unknown"
        if request.client:
            client_ip = request.client.host

        try:
            return deps.authenticator.authenticate(client_cert_pem, client_ip)
        except AuthenticationError as e:
            raise HTTPException(status_code=401, detail=str(e)) from None

    def require_localhost(request: Request) -> None:
        """Ensure request is from localhost."""
        if request.client:
            client_ip = request.client.host
            if client_ip not in ("127.0.0.1", "::1", "localhost"):
                raise HTTPException(
                    status_code=403,
                    detail="This endpoint is only accessible from localhost",
                )

    # ========================================================================
    # Public Endpoints (Pairing)
    # ========================================================================

    @app.post("/pair", response_model=PairingResponse)  # type: ignore[untyped-decorator]
    async def complete_pairing(request: Request, pairing_request: PairingRequest) -> PairingResponse:
        """Complete device pairing with a valid token.

        This endpoint is called by the mobile app after scanning the QR code.
        """
        client_ip = request.client.host if request.client else "unknown"

        try:
            result = deps.pairing.complete_pairing(
                pairing_request.token,
                pairing_request.device_name,
            )

            # Get CA cert for device to trust
            ca_cert_pem = deps.crypto.ensure_certificates().ca_cert.read_text()

            deps.audit.log_pair(
                device_id=result.device.device_id,
                device_name=result.device.name,
                ip_address=client_ip,
                success=True,
            )

            return PairingResponse(
                device_id=result.device.device_id,
                client_cert_pem=result.client_cert_pem.decode(),
                client_key_pem=result.client_key_pem.decode(),
                ca_cert_pem=ca_cert_pem,
            )

        except PairingError as e:
            deps.audit.log_pair(
                device_id="",
                device_name=pairing_request.device_name,
                ip_address=client_ip,
                success=False,
                error=str(e),
            )
            raise HTTPException(status_code=400, detail=str(e)) from None

    # ========================================================================
    # Authenticated API Endpoints
    # ========================================================================

    @app.post("/v1/chat/completions")  # type: ignore[untyped-decorator]
    async def chat_completions(
        request: Request,
        auth: MobileAuthContext = Depends(get_auth),  # noqa: B008
    ) -> JSONResponse | StreamingResponse:
        """Proxy chat completion requests to internal API."""
        body = await request.json()

        try:
            result = await deps.proxy.proxy_chat_completion(auth, body)

            execution_mode = deps.role_enforcer.get_execution_mode(auth)

            # Log successful request
            deps.audit.log_request(
                device_id=auth.device_id,
                device_name=auth.device_name,
                endpoint="/v1/chat/completions",
                execution_mode=execution_mode,
                ip_address=auth.client_ip,
                success=True,
            )

            # Handle streaming vs non-streaming response
            if isinstance(result, dict):
                return JSONResponse(result)
            else:
                # Streaming response
                return StreamingResponse(
                    result,
                    media_type="text/event-stream",
                )

        except ProxyError as e:
            deps.audit.log_request(
                device_id=auth.device_id,
                device_name=auth.device_name,
                endpoint="/v1/chat/completions",
                execution_mode="unknown",
                ip_address=auth.client_ip,
                success=False,
                error=str(e),
            )
            raise HTTPException(status_code=e.status_code, detail=str(e)) from None

    @app.get("/v1/models")  # type: ignore[untyped-decorator]
    async def list_models(
        auth: MobileAuthContext = Depends(get_auth),  # noqa: B008
    ) -> JSONResponse:
        """List available models (filtered by role)."""
        try:
            result = await deps.proxy.proxy_models(auth)
            return JSONResponse(result)
        except ProxyError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e)) from None

    @app.get("/health")  # type: ignore[untyped-decorator]
    async def health_check() -> dict[str, Any]:
        """Health check endpoint."""
        internal_ok = await deps.proxy.check_internal_api()
        return {
            "status": "healthy",
            "internal_api": "connected" if internal_ok else "disconnected",
            "timestamp": datetime.utcnow().isoformat(),
        }

    # ========================================================================
    # Configuration Endpoints (requires operator role)
    # ========================================================================

    @app.get("/v1/config")  # type: ignore[untyped-decorator]
    async def get_config(
        request: Request,
        auth: MobileAuthContext = Depends(get_auth),  # noqa: B008
    ) -> JSONResponse:
        """Get current ELLE configuration.

        Requires mobile_operator role.
        """
        try:
            result = await deps.proxy.proxy_get_config(auth)

            deps.audit.log_request(
                device_id=auth.device_id,
                device_name=auth.device_name,
                endpoint="/v1/config",
                execution_mode="config_read",
                ip_address=auth.client_ip,
                success=True,
            )

            return JSONResponse(result)

        except ProxyError as e:
            deps.audit.log_request(
                device_id=auth.device_id,
                device_name=auth.device_name,
                endpoint="/v1/config",
                execution_mode="config_read",
                ip_address=auth.client_ip,
                success=False,
                error=str(e),
            )
            raise HTTPException(status_code=e.status_code, detail=str(e)) from None

    @app.put("/v1/config")  # type: ignore[untyped-decorator]
    async def update_config(
        request: Request,
        auth: MobileAuthContext = Depends(get_auth),  # noqa: B008
    ) -> JSONResponse:
        """Update ELLE configuration.

        Requires mobile_operator role. Changes are written to the user
        config file and the daemon is signaled to reload.
        """
        body = await request.json()

        try:
            result = await deps.proxy.proxy_update_config(auth, body)

            deps.audit.log_request(
                device_id=auth.device_id,
                device_name=auth.device_name,
                endpoint="/v1/config",
                execution_mode="config_update",
                ip_address=auth.client_ip,
                success=True,
            )

            return JSONResponse(result)

        except ProxyError as e:
            deps.audit.log_request(
                device_id=auth.device_id,
                device_name=auth.device_name,
                endpoint="/v1/config",
                execution_mode="config_update",
                ip_address=auth.client_ip,
                success=False,
                error=str(e),
            )
            raise HTTPException(status_code=e.status_code, detail=str(e)) from None

    # ========================================================================
    # Local Management Endpoints (localhost only)
    # ========================================================================

    @app.get("/devices", dependencies=[Depends(require_localhost)])  # type: ignore[untyped-decorator]
    async def list_devices() -> dict[str, Any]:
        """List all paired devices (localhost only)."""
        devices = deps.store.list_devices()
        return {
            "devices": [
                safe_model_dump(
                    DeviceResponse(
                        device_id=d.device_id,
                        name=d.name,
                        role=d.role.value,
                        status=d.status.value,
                        paired_at=d.paired_at.isoformat() if d.paired_at else None,
                        last_seen_at=(d.last_seen_at.isoformat() if d.last_seen_at else None),
                    )
                )
                for d in devices
            ]
        }

    @app.delete(  # type: ignore[untyped-decorator]
        "/devices/{device_id}",
        dependencies=[Depends(require_localhost)],
    )
    async def revoke_device(device_id: str, request: Request) -> dict[str, str]:
        """Revoke a device's access (localhost only)."""
        client_ip = request.client.host if request.client else "localhost"

        device = deps.store.get_device(device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found")

        # Update status to revoked
        deps.store.update_device(device_id, status=DeviceStatus.REVOKED)

        # Revoke any elevations
        deps.elevation.revoke_elevation(device_id)

        deps.audit.log_revoke(
            device_id=device_id,
            device_name=device.name,
            ip_address=client_ip,
            success=True,
        )

        return {"status": "revoked", "device_id": device_id}

    @app.post(  # type: ignore[untyped-decorator]
        "/devices/{device_id}/elevate",
        dependencies=[Depends(require_localhost)],
    )
    async def elevate_device(
        device_id: str,
        elevate_request: ElevateRequest,
        request: Request,
    ) -> dict[str, str]:
        """Grant temporary elevation to a device (localhost only)."""
        client_ip = request.client.host if request.client else "localhost"

        device = deps.store.get_device(device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found")

        try:
            ttl_seconds = parse_ttl(elevate_request.ttl)
            elevation = deps.elevation.grant_elevation(
                device_id,
                MobileRole.MOBILE_OPERATOR,
                ttl_seconds,
                granted_by="api",
            )

            deps.audit.log_elevate(
                device_id=device_id,
                device_name=device.name,
                ip_address=client_ip,
                success=True,
            )

            return {
                "status": "elevated",
                "device_id": device_id,
                "elevated_role": elevation.elevated_role.value,
                "expires_at": elevation.expires_at.isoformat(),
            }

        except Exception as e:
            deps.audit.log_elevate(
                device_id=device_id,
                device_name=device.name,
                ip_address=client_ip,
                success=False,
                error=str(e),
            )
            raise HTTPException(status_code=400, detail=str(e)) from None

    @app.get(  # type: ignore[untyped-decorator]
        "/devices/{device_id}/status",
        dependencies=[Depends(require_localhost)],
    )
    async def device_status(device_id: str) -> dict[str, Any]:
        """Get detailed status for a device (localhost only)."""
        device = deps.store.get_device(device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found")

        elevation_status = deps.elevation.get_elevation_status(device_id)

        return {
            "device": safe_model_dump(
                DeviceResponse(
                    device_id=device.device_id,
                    name=device.name,
                    role=device.role.value,
                    status=device.status.value,
                    paired_at=device.paired_at.isoformat() if device.paired_at else None,
                    last_seen_at=(device.last_seen_at.isoformat() if device.last_seen_at else None),
                )
            ),
            "elevation": elevation_status,
        }

    @app.get("/audit", dependencies=[Depends(require_localhost)])  # type: ignore[untyped-decorator]
    async def get_audit_log(hours: int = 24, limit: int = 100) -> dict[str, Any]:
        """Get recent audit log entries (localhost only)."""
        entries = deps.audit.get_recent(hours=hours, limit=limit)
        return {
            "entries": [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "device_id": e.device_id,
                    "device_name": e.device_name,
                    "action": e.action.value,
                    "endpoint": e.endpoint,
                    "execution_mode": e.execution_mode,
                    "success": e.success,
                    "error": e.error,
                    "ip_address": e.ip_address,
                }
                for e in entries
            ]
        }

    # ========================================================================
    # Server-Sent Events (SSE) for Push Notifications
    # ========================================================================

    @app.get("/events")  # type: ignore[untyped-decorator]
    async def event_stream(
        request: Request,
        auth: MobileAuthContext = Depends(get_auth),  # noqa: B008
    ) -> StreamingResponse:
        """Subscribe to real-time notifications via Server-Sent Events.

        Mobile clients connect to this endpoint to receive push notifications
        for incidents, health alerts, plan/diff reviews, and other events.

        The connection is kept alive with periodic heartbeats. Events are
        pushed in real-time as they occur.

        Returns:
            StreamingResponse with SSE content.
        """
        from elle.daemon.notifications.mobile_push import get_mobile_notifier

        notifier = get_mobile_notifier()

        # Register this client
        client = await notifier.connect(auth.device_id, auth.device_name)

        deps.audit.log_request(
            device_id=auth.device_id,
            device_name=auth.device_name,
            endpoint="/events",
            execution_mode="subscribe",
            ip_address=auth.client_ip,
            success=True,
        )

        async def event_generator() -> AsyncGenerator[str, None]:
            """Generate SSE events for this client."""
            try:
                async for event in client.events():
                    # Check if client disconnected
                    if await request.is_disconnected():
                        break
                    yield event
            finally:
                await notifier.disconnect(auth.device_id)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
            },
        )

    @app.get("/events/status", dependencies=[Depends(require_localhost)])  # type: ignore[untyped-decorator]
    async def events_status() -> dict[str, Any]:
        """Get status of the event stream (localhost only)."""
        from elle.daemon.notifications.mobile_push import get_mobile_notifier

        notifier = get_mobile_notifier()
        return {
            "available": notifier.is_available(),
            "client_count": notifier.client_count(),
            "gateway_available": notifier._gateway_available,
        }

    return app
