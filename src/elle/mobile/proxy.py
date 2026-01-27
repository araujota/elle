"""Request proxy for ELLE Mobile Gateway.

This module handles forwarding requests from mobile devices to the
internal OpenAI-compatible API at 127.0.0.1:8377.

The proxy:
1. Filters requests based on role restrictions
2. Forwards to internal API
3. Handles SSE streaming responses
4. Logs all operations for audit
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from elle.mobile.auth import MobileAuthContext
from elle.mobile.config import MobileGatewayConfig, get_mobile_config
from elle.mobile.roles import RoleEnforcer

logger = logging.getLogger(__name__)


class ProxyError(Exception):
    """Error during request proxying."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


class RequestProxy:
    """Proxies requests to the internal ELLE API.

    Handles request filtering, forwarding, and response streaming.
    """

    def __init__(self, config: MobileGatewayConfig | None = None):
        """Initialize the proxy.

        Args:
            config: Mobile gateway configuration.
        """
        self.config = config or get_mobile_config()
        self.role_enforcer = RoleEnforcer()
        self._session: aiohttp.ClientSession | None = None

    @property
    def internal_api_url(self) -> str:
        """Get the internal API base URL."""
        return f"http://{self.config.internal_api_host}:{self.config.internal_api_port}"

    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create the HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=300)  # 5 minute timeout
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def proxy_request(
        self,
        method: str,
        path: str,
        auth: MobileAuthContext,
        body: dict[str, Any] | None = None,
        stream: bool = False,
    ) -> dict[str, Any] | AsyncIterator[bytes]:
        """Proxy a request to the internal API.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: API path (e.g., "/v1/chat/completions").
            auth: Authentication context.
            body: Request body (for POST/PUT).
            stream: Whether to stream the response.

        Returns:
            Response body as dict, or async iterator for streaming.

        Raises:
            ProxyError: If the request fails.
        """
        # Filter request based on role
        if body:
            body = self.role_enforcer.filter_request(auth, body)

        # Build internal URL
        url = f"{self.internal_api_url}{path}"

        # Add execution mode header
        execution_mode = self.role_enforcer.get_execution_mode(auth)
        headers = {
            "X-ELLE-Execution-Mode": execution_mode,
            "X-ELLE-Mobile-Device": auth.device_id,
            "X-ELLE-Mobile-Role": auth.effective_role.value,
        }

        logger.debug(
            "Proxying %s %s for device %s (mode=%s)",
            method,
            path,
            auth.device_id,
            execution_mode,
        )

        try:
            session = await self.get_session()

            if stream:
                return self._stream_request(session, method, url, headers, body)
            else:
                return await self._simple_request(session, method, url, headers, body)

        except aiohttp.ClientError as e:
            logger.error("Proxy request failed: %s", e)
            raise ProxyError(f"Internal API error: {e}", status_code=502) from e

    async def _simple_request(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Make a simple (non-streaming) request.

        Args:
            session: HTTP session.
            method: HTTP method.
            url: Full URL.
            headers: Request headers.
            body: Request body.

        Returns:
            Response body as dictionary.
        """
        async with session.request(
            method,
            url,
            json=body,
            headers=headers,
        ) as response:
            if response.status >= 400:
                error_text = await response.text()
                logger.warning("Internal API returned %d: %s", response.status, error_text)
                raise ProxyError(
                    f"Internal API error: {error_text}",
                    status_code=response.status,
                )

            result: dict[str, Any] = await response.json()
            return result

    async def _stream_request(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None,
    ) -> AsyncIterator[bytes]:
        """Make a streaming request (SSE).

        Args:
            session: HTTP session.
            method: HTTP method.
            url: Full URL.
            headers: Request headers.
            body: Request body.

        Yields:
            Response chunks as bytes.
        """
        async with session.request(
            method,
            url,
            json=body,
            headers=headers,
        ) as response:
            if response.status >= 400:
                error_text = await response.text()
                logger.warning("Internal API returned %d: %s", response.status, error_text)
                raise ProxyError(
                    f"Internal API error: {error_text}",
                    status_code=response.status,
                )

            async for chunk in response.content.iter_any():
                yield chunk

    async def proxy_chat_completion(
        self, auth: MobileAuthContext, body: dict[str, Any]
    ) -> dict[str, Any] | AsyncIterator[bytes]:
        """Proxy a chat completion request.

        Args:
            auth: Authentication context.
            body: Request body.

        Returns:
            Response or stream iterator.
        """
        stream = body.get("stream", False)
        return await self.proxy_request(
            "POST",
            "/v1/chat/completions",
            auth,
            body=body,
            stream=stream,
        )

    async def proxy_models(self, auth: MobileAuthContext) -> dict[str, Any]:
        """Proxy a models list request.

        Args:
            auth: Authentication context.

        Returns:
            List of available models (filtered by role).
        """
        result = await self.proxy_request(
            "GET",
            "/v1/models",
            auth,
        )

        # This is a non-streaming request, so result is always a dict
        response: dict[str, Any] = result  # type: ignore[assignment]

        # Filter models based on role
        from elle.mobile.roles import ROLE_ALLOWED_MODELS

        allowed_models = ROLE_ALLOWED_MODELS.get(auth.effective_role, set())

        if "data" in response:
            response["data"] = [model for model in response["data"] if model.get("id") in allowed_models]

        return response

    async def check_internal_api(self) -> bool:
        """Check if internal API is available.

        Returns:
            True if API is responding.
        """
        try:
            session = await self.get_session()
            async with session.get(
                f"{self.internal_api_url}/health",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                return bool(response.status == 200)
        except Exception:
            return False

    async def proxy_get_config(self, auth: MobileAuthContext) -> dict[str, Any]:
        """Get current ELLE configuration.

        Args:
            auth: Authentication context.

        Returns:
            Configuration dictionary.

        Raises:
            ProxyError: If not authorized or request fails.
        """
        from elle.mobile.config_manager import ConfigError, get_config_manager

        try:
            manager = get_config_manager()
            return manager.get_serialized_config(auth.effective_role)
        except ConfigError as e:
            raise ProxyError(str(e), status_code=403) from e

    async def proxy_update_config(self, auth: MobileAuthContext, updates: dict[str, Any]) -> dict[str, Any]:
        """Update ELLE configuration.

        Args:
            auth: Authentication context.
            updates: Configuration updates to apply.

        Returns:
            Result dictionary with status.

        Raises:
            ProxyError: If not authorized or validation fails.
        """
        from elle.mobile.config_manager import ConfigError, get_config_manager

        try:
            manager = get_config_manager()
            return manager.update_config(updates, auth.effective_role)
        except ConfigError as e:
            raise ProxyError(str(e), status_code=400) from e
