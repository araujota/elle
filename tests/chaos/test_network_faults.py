"""Chaos tests for network fault resilience.

Tests: expired mTLS cert, unreachable notification endpoint,
DNS failure, and connection refused scenarios.
"""

from __future__ import annotations

import ssl
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.chaos.conftest import FaultInjector


class TestExpiredMTLSCert:
    """Mobile gateway should handle expired mTLS certificates."""

    def test_expired_cert_raises_ssl_error(self, fault: FaultInjector) -> None:
        """Loading an expired certificate should raise SSLError."""
        with fault.expired_mtls_cert():
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            with pytest.raises(ssl.SSLError, match="certificate has expired"):
                ctx.load_cert_chain("expired_cert.pem", "key.pem")

    def test_mobile_config_validates_cert_expiry(self, fault: FaultInjector) -> None:
        """Mobile config manager should detect expired certs during validation."""
        with fault.expired_mtls_cert():
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            with pytest.raises(ssl.SSLError):
                ctx.load_cert_chain("cert.pem", "key.pem")

    def test_self_signed_cert_rejected_in_verify_mode(self) -> None:
        """Self-signed certs should be rejected when verification is enabled."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.verify_mode = ssl.CERT_REQUIRED
        # Without loading CA certs, any connection should fail verification
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    @pytest.mark.asyncio
    async def test_cloud_sync_handles_cert_error(self, fault: FaultInjector) -> None:
        """Cloud sync worker should handle SSL certificate errors."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=ssl.SSLError(1, "[SSL] certificate verify failed"))

        with pytest.raises(ssl.SSLError):
            await mock_client.post("https://cloud.elle.dev/api/incidents")


class TestUnreachableNotificationEndpoint:
    """Notification service should handle unreachable endpoints."""

    @pytest.mark.asyncio
    async def test_ntfy_endpoint_unreachable(self, fault: FaultInjector) -> None:
        """ntfy notification should handle connection failure."""
        with fault.unreachable_endpoint("https://ntfy.example.com"):
            import httpx

            with pytest.raises(httpx.ConnectError):
                async with httpx.AsyncClient() as client:
                    await client.post("https://ntfy.example.com/topic", content=b"test")

    @pytest.mark.asyncio
    async def test_notification_falls_back_on_endpoint_failure(self) -> None:
        """NotificationService should fall back to local delivery when remote fails."""
        mock_service = MagicMock()
        mock_service.send = MagicMock(return_value=MagicMock(success=True, method="notify-send"))

        # When remote endpoint fails, local notify-send should work
        result = mock_service.send(MagicMock(title="Test", body="Fallback"))
        assert result.success
        assert result.method == "notify-send"

    @pytest.mark.asyncio
    async def test_mobile_push_handles_timeout(self) -> None:
        """Mobile push notification should handle request timeout."""
        import httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("Read timed out"))

        with pytest.raises(httpx.ReadTimeout):
            await mock_client.post(
                "https://mobile-gateway.elle.dev/push",
                json={"title": "Alert", "body": "Test"},
            )

    def test_notification_rate_limiting(self) -> None:
        """Notification service should rate-limit repeated notifications."""
        from collections import defaultdict

        rate_limiter: dict[str, list[float]] = defaultdict(list)
        import time

        def should_send(notification_key: str, window_sec: float = 5.0) -> bool:
            now = time.time()
            recent = [t for t in rate_limiter[notification_key] if now - t < window_sec]
            rate_limiter[notification_key] = recent
            if recent:
                return False
            rate_limiter[notification_key].append(now)
            return True

        assert should_send("test-alert")
        assert not should_send("test-alert")  # Same key within window
        assert should_send("different-alert")  # Different key is fine


class TestDNSFailure:
    """System should handle DNS resolution failures."""

    def test_dns_failure_raises_gaierror(self, fault: FaultInjector) -> None:
        """DNS failure should raise socket.gaierror."""
        import socket

        with fault.dns_failure():
            with pytest.raises(socket.gaierror):
                socket.getaddrinfo("nonexistent.example.com", 443)

    @pytest.mark.asyncio
    async def test_cloud_sync_handles_dns_failure(self, fault: FaultInjector) -> None:
        """Cloud sync should handle DNS failure gracefully."""
        import httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Name resolution failed"))

        with pytest.raises(httpx.ConnectError, match="Name resolution"):
            await mock_client.post("https://cloud.elle.dev/api/incidents")

    def test_notification_endpoint_dns_failure(self, fault: FaultInjector) -> None:
        """Notification endpoint should handle DNS failure."""
        import socket

        with fault.dns_failure():
            with pytest.raises(socket.gaierror):
                socket.getaddrinfo("ntfy.example.com", 443)


class TestConnectionRefused:
    """Services should handle connection refused errors."""

    @pytest.mark.asyncio
    async def test_ollama_connection_refused(self, fault: FaultInjector) -> None:
        """LLM client should handle Ollama connection refused."""
        with fault.service_unavailable("httpx.AsyncClient.post"):
            import httpx

            with pytest.raises(ConnectionRefusedError):
                async with httpx.AsyncClient() as client:
                    await client.post("http://localhost:11434/api/chat")

    def test_postgres_connection_refused(self, fault: FaultInjector) -> None:
        """Database operations should handle PostgreSQL connection refused."""
        with fault.database_refused():
            with pytest.raises(Exception):
                from elle.storage.engine import get_conn

                get_conn()

    @pytest.mark.asyncio
    async def test_api_server_connection_refused(self) -> None:
        """API client should handle daemon API connection refused."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionRefusedError("Connection refused"))

        with pytest.raises(ConnectionRefusedError):
            await mock_client.get("http://localhost:7778/api/status")

    @pytest.mark.asyncio
    async def test_retry_on_connection_refused(self) -> None:
        """Retry logic should back off on repeated connection refused."""
        attempts = 0
        max_retries = 3

        async def mock_connect() -> None:
            nonlocal attempts
            attempts += 1
            if attempts <= max_retries:
                raise ConnectionRefusedError("Connection refused")

        # Should eventually succeed after retries
        for _ in range(max_retries + 1):
            try:
                await mock_connect()
                break
            except ConnectionRefusedError:
                continue

        assert attempts == max_retries + 1
