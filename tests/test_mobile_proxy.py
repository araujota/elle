from __future__ import annotations

"""Tests for elle.mobile.proxy -- RequestProxy and ProxyError.

Covers:
- ProxyError construction with message and status_code
- RequestProxy.__init__ with explicit config and default fallback
- RequestProxy.internal_api_url property
- RequestProxy.get_session creates/reuses aiohttp session
- RequestProxy.close tears down the session
- RequestProxy.proxy_request for simple and streaming modes
- RequestProxy.proxy_request role filtering, header injection
- RequestProxy.proxy_request error handling (ClientError -> ProxyError)
- RequestProxy._simple_request success and HTTP error paths
- RequestProxy._stream_request success and HTTP error paths
- RequestProxy.proxy_chat_completion with stream=True and stream=False
- RequestProxy.proxy_models filters models by role
- RequestProxy.check_internal_api success, failure, and exception paths
- RequestProxy.proxy_get_config success and ConfigError paths
- RequestProxy.proxy_update_config success and ConfigError paths
"""

import sys
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub out heavy dependencies that are not under test but are imported at
# module level.  We must install these BEFORE importing the module under test.
# ---------------------------------------------------------------------------
# 1. elle.mobile.crypto requires the 'cryptography' C-extension.
import elle.mobile

if "elle.mobile.crypto" not in sys.modules:
    _crypto_stub = MagicMock()
    sys.modules["elle.mobile.crypto"] = _crypto_stub
    elle.mobile.crypto = _crypto_stub  # type: ignore[attr-defined]


# 2. aiohttp may not be installed in the test environment.  The proxy module
#    uses aiohttp.ClientSession, aiohttp.ClientTimeout, and catches
#    aiohttp.ClientError.  We need real exception classes so that ``except
#    aiohttp.ClientError`` works correctly in tests.
class _StubClientError(Exception):
    """Stand-in for aiohttp.ClientError when aiohttp is not installed."""


if "aiohttp" not in sys.modules:
    _aiohttp_stub = MagicMock()
    _aiohttp_stub.ClientError = _StubClientError
    _aiohttp_stub.ClientTimeout = MagicMock
    _aiohttp_stub.ClientSession = MagicMock
    sys.modules["aiohttp"] = _aiohttp_stub

# ---------------------------------------------------------------------------
# NOW it is safe to import the module under test and companions.
# ---------------------------------------------------------------------------

import aiohttp  # noqa: E402  (the real or stubbed module)

from elle.mobile.auth import MobileAuthContext  # noqa: E402
from elle.mobile.config import MobileGatewayConfig  # noqa: E402
from elle.mobile.models import MobileRole  # noqa: E402
from elle.mobile.proxy import ProxyError, RequestProxy  # noqa: E402

# =============================================================================
# Shared helpers
# =============================================================================

FAKE_FINGERPRINT = "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99"
CLIENT_IP = "192.168.1.42"


def _make_config(
    internal_api_host: str = "127.0.0.1",
    internal_api_port: int = 8377,
) -> MobileGatewayConfig:
    """Build a MobileGatewayConfig with sensible defaults."""
    return MobileGatewayConfig(
        internal_api_host=internal_api_host,
        internal_api_port=internal_api_port,
    )


def _make_auth(
    device_id: str = "dev-001",
    device_name: str = "Test Phone",
    role: MobileRole = MobileRole.MOBILE_READONLY,
    effective_role: MobileRole | None = None,
) -> MobileAuthContext:
    """Build a MobileAuthContext with sensible defaults."""
    return MobileAuthContext(
        device_id=device_id,
        device_name=device_name,
        role=role,
        effective_role=effective_role or role,
        client_ip=CLIENT_IP,
        cert_fingerprint=FAKE_FINGERPRINT,
    )


def _make_proxy(config: MobileGatewayConfig | None = None) -> RequestProxy:
    """Build a RequestProxy with an explicit config (avoids global lookup)."""
    return RequestProxy(config=config or _make_config())


# =============================================================================
# TestProxyError
# =============================================================================


class TestProxyError:
    """Tests for the ProxyError exception."""

    def test_default_status_code(self):
        """ProxyError defaults to status_code 500."""
        err = ProxyError("something broke")
        assert str(err) == "something broke"
        assert err.status_code == 500

    def test_custom_status_code(self):
        """ProxyError accepts a custom status_code."""
        err = ProxyError("bad gateway", status_code=502)
        assert str(err) == "bad gateway"
        assert err.status_code == 502

    def test_is_exception_subclass(self):
        """ProxyError is a subclass of Exception."""
        assert issubclass(ProxyError, Exception)


# =============================================================================
# TestRequestProxyInit
# =============================================================================


class TestRequestProxyInit:
    """Tests for RequestProxy.__init__."""

    def test_explicit_config_used(self):
        """When a config is provided, it is used directly."""
        cfg = _make_config(internal_api_host="10.0.0.1", internal_api_port=9999)
        proxy = RequestProxy(config=cfg)
        assert proxy.config is cfg
        assert proxy._session is None

    @patch("elle.mobile.proxy.get_mobile_config")
    def test_default_config_loaded(self, mock_get_config):
        """When config is None, get_mobile_config() provides the default."""
        mock_cfg = MagicMock()
        mock_get_config.return_value = mock_cfg

        proxy = RequestProxy(config=None)

        mock_get_config.assert_called_once()
        assert proxy.config is mock_cfg

    @patch("elle.mobile.proxy.RoleEnforcer")
    def test_role_enforcer_created(self, mock_enforcer_cls):
        """A RoleEnforcer is instantiated during __init__."""
        proxy = _make_proxy()
        mock_enforcer_cls.assert_called_once()
        assert proxy.role_enforcer is mock_enforcer_cls.return_value


# =============================================================================
# TestInternalApiUrl
# =============================================================================


class TestInternalApiUrl:
    """Tests for the internal_api_url property."""

    def test_url_format(self):
        """URL is built from config host and port."""
        proxy = _make_proxy(_make_config("10.0.0.5", 1234))
        assert proxy.internal_api_url == "http://10.0.0.5:1234"

    def test_default_url(self):
        """Default config yields localhost:8377."""
        proxy = _make_proxy()
        assert proxy.internal_api_url == "http://127.0.0.1:8377"


# =============================================================================
# TestGetSession
# =============================================================================


class TestGetSession:
    """Tests for get_session()."""

    @pytest.mark.asyncio
    @patch("elle.mobile.proxy.aiohttp.ClientSession")
    async def test_creates_session_when_none(self, mock_session_cls):
        """A new aiohttp.ClientSession is created when _session is None."""
        mock_session_instance = MagicMock()
        mock_session_instance.closed = False
        mock_session_cls.return_value = mock_session_instance

        proxy = _make_proxy()
        session = await proxy.get_session()

        mock_session_cls.assert_called_once()
        assert session is mock_session_instance
        assert proxy._session is mock_session_instance

    @pytest.mark.asyncio
    @patch("elle.mobile.proxy.aiohttp.ClientSession")
    async def test_reuses_existing_open_session(self, mock_session_cls):
        """An existing non-closed session is returned without creating a new one."""
        existing = MagicMock()
        existing.closed = False

        proxy = _make_proxy()
        proxy._session = existing

        session = await proxy.get_session()

        mock_session_cls.assert_not_called()
        assert session is existing

    @pytest.mark.asyncio
    @patch("elle.mobile.proxy.aiohttp.ClientSession")
    async def test_creates_new_session_when_closed(self, mock_session_cls):
        """When the existing session is closed, a new one is created."""
        old_session = MagicMock()
        old_session.closed = True

        new_session = MagicMock()
        new_session.closed = False
        mock_session_cls.return_value = new_session

        proxy = _make_proxy()
        proxy._session = old_session

        session = await proxy.get_session()

        mock_session_cls.assert_called_once()
        assert session is new_session


# =============================================================================
# TestClose
# =============================================================================


class TestClose:
    """Tests for close()."""

    @pytest.mark.asyncio
    async def test_close_open_session(self):
        """Closing an open session calls session.close() and sets _session to None."""
        mock_session = AsyncMock()
        mock_session.closed = False

        proxy = _make_proxy()
        proxy._session = mock_session

        await proxy.close()

        mock_session.close.assert_awaited_once()
        assert proxy._session is None

    @pytest.mark.asyncio
    async def test_close_already_closed_session(self):
        """If session is already closed, close() is a no-op."""
        mock_session = AsyncMock()
        mock_session.closed = True

        proxy = _make_proxy()
        proxy._session = mock_session

        await proxy.close()

        mock_session.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_close_no_session(self):
        """If there is no session, close() is a no-op."""
        proxy = _make_proxy()
        assert proxy._session is None

        await proxy.close()  # Should not raise

        assert proxy._session is None


# =============================================================================
# TestProxyRequest
# =============================================================================


class TestProxyRequest:
    """Tests for proxy_request()."""

    @pytest.mark.asyncio
    async def test_simple_request_path(self):
        """Non-streaming proxy_request delegates to _simple_request."""
        auth = _make_auth(effective_role=MobileRole.MOBILE_READONLY)
        body = {"model": "elle.readonly", "messages": []}

        proxy = _make_proxy()
        proxy.role_enforcer = MagicMock()
        proxy.role_enforcer.filter_request.return_value = body
        proxy.role_enforcer.get_execution_mode.return_value = "readonly"

        expected_result = {"id": "chatcmpl-1", "choices": []}
        proxy._simple_request = AsyncMock(return_value=expected_result)

        mock_session = MagicMock()
        proxy.get_session = AsyncMock(return_value=mock_session)

        result = await proxy.proxy_request("POST", "/v1/chat/completions", auth, body=body, stream=False)

        assert result == expected_result
        proxy.role_enforcer.filter_request.assert_called_once_with(auth, body)
        proxy._simple_request.assert_awaited_once()

        call_args = proxy._simple_request.call_args
        assert call_args[0][0] is mock_session
        assert call_args[0][1] == "POST"
        assert "/v1/chat/completions" in call_args[0][2]
        headers = call_args[0][3]
        assert headers["X-ELLE-Execution-Mode"] == "readonly"
        assert headers["X-ELLE-Mobile-Device"] == "dev-001"
        assert headers["X-ELLE-Mobile-Role"] == "mobile_readonly"

    @pytest.mark.asyncio
    async def test_streaming_request_path(self):
        """Streaming proxy_request delegates to _stream_request."""
        auth = _make_auth(effective_role=MobileRole.MOBILE_OPERATOR)
        body = {"model": "elle", "messages": [], "stream": True}

        proxy = _make_proxy()
        proxy.role_enforcer = MagicMock()
        proxy.role_enforcer.filter_request.return_value = body
        proxy.role_enforcer.get_execution_mode.return_value = "execute"

        sentinel = object()
        proxy._stream_request = MagicMock(return_value=sentinel)

        mock_session = MagicMock()
        proxy.get_session = AsyncMock(return_value=mock_session)

        result = await proxy.proxy_request("POST", "/v1/chat/completions", auth, body=body, stream=True)

        assert result is sentinel
        proxy._stream_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_body_skips_filter(self):
        """When body is None, role_enforcer.filter_request is not called."""
        auth = _make_auth()

        proxy = _make_proxy()
        proxy.role_enforcer = MagicMock()
        proxy.role_enforcer.get_execution_mode.return_value = "readonly"

        expected = {"data": []}
        proxy._simple_request = AsyncMock(return_value=expected)

        mock_session = MagicMock()
        proxy.get_session = AsyncMock(return_value=mock_session)

        result = await proxy.proxy_request("GET", "/v1/models", auth, body=None, stream=False)

        assert result == expected
        proxy.role_enforcer.filter_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_client_error_raises_proxy_error(self):
        """An aiohttp.ClientError is caught and re-raised as ProxyError(502)."""

        auth = _make_auth()

        proxy = _make_proxy()
        proxy.role_enforcer = MagicMock()
        proxy.role_enforcer.get_execution_mode.return_value = "readonly"

        proxy.get_session = AsyncMock(side_effect=aiohttp.ClientError("connection refused"))

        with pytest.raises(ProxyError) as exc_info:
            await proxy.proxy_request("GET", "/v1/models", auth)

        assert exc_info.value.status_code == 502
        assert "Internal API error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_headers_include_device_info(self):
        """Request headers include execution mode, device id, and role."""
        auth = _make_auth(
            device_id="dev-xyz",
            effective_role=MobileRole.MOBILE_OPERATOR,
        )

        proxy = _make_proxy()
        proxy.role_enforcer = MagicMock()
        proxy.role_enforcer.get_execution_mode.return_value = "execute"

        proxy._simple_request = AsyncMock(return_value={})
        mock_session = MagicMock()
        proxy.get_session = AsyncMock(return_value=mock_session)

        await proxy.proxy_request("GET", "/test", auth, body=None, stream=False)

        call_args = proxy._simple_request.call_args
        headers = call_args[0][3]
        assert headers["X-ELLE-Execution-Mode"] == "execute"
        assert headers["X-ELLE-Mobile-Device"] == "dev-xyz"
        assert headers["X-ELLE-Mobile-Role"] == "mobile_operator"

    @pytest.mark.asyncio
    async def test_url_built_from_internal_api(self):
        """The full URL is built by combining internal_api_url with the path."""
        auth = _make_auth()
        cfg = _make_config("192.168.1.1", 5555)
        proxy = _make_proxy(cfg)
        proxy.role_enforcer = MagicMock()
        proxy.role_enforcer.get_execution_mode.return_value = "readonly"

        proxy._simple_request = AsyncMock(return_value={})
        mock_session = MagicMock()
        proxy.get_session = AsyncMock(return_value=mock_session)

        await proxy.proxy_request("GET", "/v1/models", auth, body=None, stream=False)

        call_args = proxy._simple_request.call_args
        url = call_args[0][2]
        assert url == "http://192.168.1.1:5555/v1/models"


# =============================================================================
# TestSimpleRequest
# =============================================================================


class TestSimpleRequest:
    """Tests for _simple_request()."""

    @pytest.mark.asyncio
    async def test_success_returns_json(self):
        """A successful response (status < 400) returns parsed JSON."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"id": "chatcmpl-1", "choices": []})

        @asynccontextmanager
        async def fake_request(method, url, json=None, headers=None):
            yield mock_response

        mock_session = MagicMock()
        mock_session.request = fake_request

        proxy = _make_proxy()
        result = await proxy._simple_request(
            mock_session,
            "POST",
            "http://127.0.0.1:8377/v1/chat/completions",
            {"X-ELLE-Execution-Mode": "readonly"},
            {"model": "elle.readonly"},
        )

        assert result == {"id": "chatcmpl-1", "choices": []}
        mock_response.json.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_status_raises_proxy_error(self):
        """A response with status >= 400 raises ProxyError with that status code."""
        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.text = AsyncMock(return_value="Not Found")

        @asynccontextmanager
        async def fake_request(method, url, json=None, headers=None):
            yield mock_response

        mock_session = MagicMock()
        mock_session.request = fake_request

        proxy = _make_proxy()
        with pytest.raises(ProxyError) as exc_info:
            await proxy._simple_request(
                mock_session,
                "GET",
                "http://127.0.0.1:8377/v1/missing",
                {},
                None,
            )

        assert exc_info.value.status_code == 404
        assert "Not Found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_500_error_status(self):
        """A 500 response raises ProxyError with status_code=500."""
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")

        @asynccontextmanager
        async def fake_request(method, url, json=None, headers=None):
            yield mock_response

        mock_session = MagicMock()
        mock_session.request = fake_request

        proxy = _make_proxy()
        with pytest.raises(ProxyError) as exc_info:
            await proxy._simple_request(
                mock_session,
                "POST",
                "http://127.0.0.1:8377/v1/chat/completions",
                {},
                {"model": "elle"},
            )

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_passes_body_as_json(self):
        """The body is forwarded as the json= parameter."""
        captured_kwargs: dict[str, Any] = {}
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={})

        @asynccontextmanager
        async def fake_request(method, url, json=None, headers=None):
            captured_kwargs["json"] = json
            captured_kwargs["headers"] = headers
            yield mock_response

        mock_session = MagicMock()
        mock_session.request = fake_request

        body = {"model": "elle", "messages": [{"role": "user", "content": "hello"}]}
        proxy = _make_proxy()
        await proxy._simple_request(
            mock_session,
            "POST",
            "http://127.0.0.1:8377/v1/chat/completions",
            {"X-Test": "value"},
            body,
        )

        assert captured_kwargs["json"] is body
        assert captured_kwargs["headers"] == {"X-Test": "value"}


# =============================================================================
# TestStreamRequest
# =============================================================================


class TestStreamRequest:
    """Tests for _stream_request()."""

    @pytest.mark.asyncio
    async def test_success_yields_chunks(self):
        """A successful streaming response yields all chunks."""
        chunks = [b"data: chunk1\n\n", b"data: chunk2\n\n", b"data: [DONE]\n\n"]

        async def fake_iter_any():
            for chunk in chunks:
                yield chunk

        mock_content = MagicMock()
        mock_content.iter_any = fake_iter_any

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.content = mock_content

        @asynccontextmanager
        async def fake_request(method, url, json=None, headers=None):
            yield mock_response

        mock_session = MagicMock()
        mock_session.request = fake_request

        proxy = _make_proxy()
        received = []
        async for chunk in proxy._stream_request(
            mock_session,
            "POST",
            "http://127.0.0.1:8377/v1/chat/completions",
            {},
            {"model": "elle", "stream": True},
        ):
            received.append(chunk)

        assert received == chunks

    @pytest.mark.asyncio
    async def test_error_status_raises_proxy_error(self):
        """A streaming response with status >= 400 raises ProxyError."""
        mock_response = AsyncMock()
        mock_response.status = 503
        mock_response.text = AsyncMock(return_value="Service Unavailable")

        @asynccontextmanager
        async def fake_request(method, url, json=None, headers=None):
            yield mock_response

        mock_session = MagicMock()
        mock_session.request = fake_request

        proxy = _make_proxy()
        with pytest.raises(ProxyError) as exc_info:
            async for _ in proxy._stream_request(
                mock_session,
                "POST",
                "http://127.0.0.1:8377/v1/chat/completions",
                {},
                {"model": "elle"},
            ):
                pass  # pragma: no cover

        assert exc_info.value.status_code == 503
        assert "Service Unavailable" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_empty_stream(self):
        """An empty stream yields no chunks."""

        async def fake_iter_any():
            return
            yield  # noqa: RET504 – required for async generator

        mock_content = MagicMock()
        mock_content.iter_any = fake_iter_any

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.content = mock_content

        @asynccontextmanager
        async def fake_request(method, url, json=None, headers=None):
            yield mock_response

        mock_session = MagicMock()
        mock_session.request = fake_request

        proxy = _make_proxy()
        received = []
        async for chunk in proxy._stream_request(
            mock_session,
            "POST",
            "http://127.0.0.1:8377/v1/chat/completions",
            {},
            {"model": "elle"},
        ):
            received.append(chunk)

        assert received == []


# =============================================================================
# TestProxyChatCompletion
# =============================================================================


class TestProxyChatCompletion:
    """Tests for proxy_chat_completion()."""

    @pytest.mark.asyncio
    async def test_non_streaming(self):
        """proxy_chat_completion with stream=False calls proxy_request correctly."""
        auth = _make_auth()
        body = {"model": "elle.readonly", "messages": [{"role": "user", "content": "hi"}]}
        expected = {"id": "chatcmpl-1", "choices": []}

        proxy = _make_proxy()
        proxy.proxy_request = AsyncMock(return_value=expected)

        result = await proxy.proxy_chat_completion(auth, body)

        assert result == expected
        proxy.proxy_request.assert_awaited_once_with(
            "POST",
            "/v1/chat/completions",
            auth,
            body=body,
            stream=False,
        )

    @pytest.mark.asyncio
    async def test_streaming(self):
        """proxy_chat_completion with stream=True passes stream=True to proxy_request."""
        auth = _make_auth()
        body = {"model": "elle.readonly", "messages": [], "stream": True}
        sentinel = object()

        proxy = _make_proxy()
        proxy.proxy_request = AsyncMock(return_value=sentinel)

        result = await proxy.proxy_chat_completion(auth, body)

        assert result is sentinel
        proxy.proxy_request.assert_awaited_once_with(
            "POST",
            "/v1/chat/completions",
            auth,
            body=body,
            stream=True,
        )

    @pytest.mark.asyncio
    async def test_stream_default_false(self):
        """When body lacks 'stream' key, it defaults to False."""
        auth = _make_auth()
        body = {"model": "elle.readonly", "messages": []}

        proxy = _make_proxy()
        proxy.proxy_request = AsyncMock(return_value={})

        await proxy.proxy_chat_completion(auth, body)

        proxy.proxy_request.assert_awaited_once_with(
            "POST",
            "/v1/chat/completions",
            auth,
            body=body,
            stream=False,
        )


# =============================================================================
# TestProxyModels
# =============================================================================


class TestProxyModels:
    """Tests for proxy_models()."""

    @pytest.mark.asyncio
    async def test_readonly_role_filters_models(self):
        """A readonly device only sees elle.readonly and elle-readonly models."""
        auth = _make_auth(effective_role=MobileRole.MOBILE_READONLY)
        all_models = {
            "data": [
                {"id": "elle"},
                {"id": "elle.readonly"},
                {"id": "elle-readonly"},
                {"id": "gpt-4"},
            ]
        }

        proxy = _make_proxy()
        proxy.proxy_request = AsyncMock(return_value=all_models)

        result = await proxy.proxy_models(auth)

        model_ids = {m["id"] for m in result["data"]}
        assert model_ids == {"elle.readonly", "elle-readonly"}

    @pytest.mark.asyncio
    async def test_operator_role_filters_models(self):
        """An operator device sees elle, elle.readonly, and elle-readonly models."""
        auth = _make_auth(effective_role=MobileRole.MOBILE_OPERATOR)
        all_models = {
            "data": [
                {"id": "elle"},
                {"id": "elle.readonly"},
                {"id": "elle-readonly"},
                {"id": "gpt-4"},
                {"id": "other-model"},
            ]
        }

        proxy = _make_proxy()
        proxy.proxy_request = AsyncMock(return_value=all_models)

        result = await proxy.proxy_models(auth)

        model_ids = {m["id"] for m in result["data"]}
        assert model_ids == {"elle", "elle.readonly", "elle-readonly"}

    @pytest.mark.asyncio
    async def test_no_data_key_returns_unmodified(self):
        """If the response lacks a 'data' key, it is returned as-is."""
        auth = _make_auth(effective_role=MobileRole.MOBILE_READONLY)
        response = {"object": "list"}

        proxy = _make_proxy()
        proxy.proxy_request = AsyncMock(return_value=response)

        result = await proxy.proxy_models(auth)

        assert result == {"object": "list"}
        assert "data" not in result

    @pytest.mark.asyncio
    async def test_calls_proxy_request_with_get(self):
        """proxy_models calls proxy_request with GET /v1/models."""
        auth = _make_auth()

        proxy = _make_proxy()
        proxy.proxy_request = AsyncMock(return_value={"data": []})

        await proxy.proxy_models(auth)

        proxy.proxy_request.assert_awaited_once_with(
            "GET",
            "/v1/models",
            auth,
        )

    @pytest.mark.asyncio
    async def test_empty_data_list(self):
        """An empty data list remains empty after filtering."""
        auth = _make_auth(effective_role=MobileRole.MOBILE_READONLY)

        proxy = _make_proxy()
        proxy.proxy_request = AsyncMock(return_value={"data": []})

        result = await proxy.proxy_models(auth)

        assert result["data"] == []

    @pytest.mark.asyncio
    async def test_model_without_id_filtered_out(self):
        """Models missing 'id' field are filtered out by the in-check."""
        auth = _make_auth(effective_role=MobileRole.MOBILE_READONLY)
        response = {
            "data": [
                {"id": "elle.readonly"},
                {"name": "no-id-model"},  # No 'id' key
            ]
        }

        proxy = _make_proxy()
        proxy.proxy_request = AsyncMock(return_value=response)

        result = await proxy.proxy_models(auth)

        assert len(result["data"]) == 1
        assert result["data"][0]["id"] == "elle.readonly"


# =============================================================================
# TestCheckInternalApi
# =============================================================================


class TestCheckInternalApi:
    """Tests for check_internal_api()."""

    @pytest.mark.asyncio
    async def test_healthy_api_returns_true(self):
        """When /health returns 200, check_internal_api returns True."""
        mock_response = AsyncMock()
        mock_response.status = 200

        @asynccontextmanager
        async def fake_get(url, timeout=None):
            yield mock_response

        mock_session = MagicMock()
        mock_session.get = fake_get

        proxy = _make_proxy()
        proxy.get_session = AsyncMock(return_value=mock_session)

        result = await proxy.check_internal_api()

        assert result is True

    @pytest.mark.asyncio
    async def test_unhealthy_api_returns_false(self):
        """When /health returns non-200, check_internal_api returns False."""
        mock_response = AsyncMock()
        mock_response.status = 503

        @asynccontextmanager
        async def fake_get(url, timeout=None):
            yield mock_response

        mock_session = MagicMock()
        mock_session.get = fake_get

        proxy = _make_proxy()
        proxy.get_session = AsyncMock(return_value=mock_session)

        result = await proxy.check_internal_api()

        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        """When any exception occurs, check_internal_api returns False."""
        proxy = _make_proxy()
        proxy.get_session = AsyncMock(side_effect=Exception("connection refused"))

        result = await proxy.check_internal_api()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_url_includes_internal_api(self):
        """The health check URL uses the configured internal API base."""
        cfg = _make_config("10.0.0.99", 7777)
        captured_url: list[str] = []

        mock_response = AsyncMock()
        mock_response.status = 200

        @asynccontextmanager
        async def fake_get(url, timeout=None):
            captured_url.append(url)
            yield mock_response

        mock_session = MagicMock()
        mock_session.get = fake_get

        proxy = _make_proxy(cfg)
        proxy.get_session = AsyncMock(return_value=mock_session)

        await proxy.check_internal_api()

        assert captured_url[0] == "http://10.0.0.99:7777/health"


# =============================================================================
# TestProxyGetConfig
# =============================================================================


class TestProxyGetConfig:
    """Tests for proxy_get_config()."""

    @pytest.mark.asyncio
    async def test_success_returns_config(self):
        """Successful config retrieval returns serialized config dict."""
        auth = _make_auth(effective_role=MobileRole.MOBILE_OPERATOR)
        expected = {"daemon": {"log_level": "INFO"}, "mobile": {"enabled": True}}

        mock_manager = MagicMock()
        mock_manager.get_serialized_config.return_value = expected

        proxy = _make_proxy()

        with patch("elle.mobile.config_manager.get_config_manager", return_value=mock_manager):
            result = await proxy.proxy_get_config(auth)

        assert result == expected
        mock_manager.get_serialized_config.assert_called_once_with(MobileRole.MOBILE_OPERATOR)

    @pytest.mark.asyncio
    async def test_config_error_raises_proxy_error_403(self):
        """A ConfigError is caught and re-raised as ProxyError(403)."""
        from elle.mobile.config_manager import ConfigError

        auth = _make_auth(effective_role=MobileRole.MOBILE_READONLY)

        mock_manager = MagicMock()
        mock_manager.get_serialized_config.side_effect = ConfigError("Configuration access requires operator role")

        proxy = _make_proxy()

        with patch("elle.mobile.config_manager.get_config_manager", return_value=mock_manager):
            with pytest.raises(ProxyError) as exc_info:
                await proxy.proxy_get_config(auth)

        assert exc_info.value.status_code == 403
        assert "operator role" in str(exc_info.value)


# =============================================================================
# TestProxyUpdateConfig
# =============================================================================


class TestProxyUpdateConfig:
    """Tests for proxy_update_config()."""

    @pytest.mark.asyncio
    async def test_success_returns_result(self):
        """A successful config update returns the result dict from ConfigManager."""
        auth = _make_auth(effective_role=MobileRole.MOBILE_OPERATOR)
        updates = {"daemon": {"log_level": "DEBUG"}}
        expected = {"status": "updated", "reload_success": True, "restart_required": False}

        mock_manager = MagicMock()
        mock_manager.update_config.return_value = expected

        proxy = _make_proxy()

        with patch("elle.mobile.config_manager.get_config_manager", return_value=mock_manager):
            result = await proxy.proxy_update_config(auth, updates)

        assert result == expected
        mock_manager.update_config.assert_called_once_with(updates, MobileRole.MOBILE_OPERATOR)

    @pytest.mark.asyncio
    async def test_config_error_raises_proxy_error_400(self):
        """A ConfigError during update is caught and re-raised as ProxyError(400)."""
        from elle.mobile.config_manager import ConfigError

        auth = _make_auth(effective_role=MobileRole.MOBILE_OPERATOR)
        updates = {"daemon": {"port_probe_interval": 1}}

        mock_manager = MagicMock()
        mock_manager.update_config.side_effect = ConfigError("Validation failed: interval too low")

        proxy = _make_proxy()

        with patch("elle.mobile.config_manager.get_config_manager", return_value=mock_manager):
            with pytest.raises(ProxyError) as exc_info:
                await proxy.proxy_update_config(auth, updates)

        assert exc_info.value.status_code == 400
        assert "Validation failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_readonly_role_raises_proxy_error(self):
        """A readonly role causes ConfigError -> ProxyError(400)."""
        from elle.mobile.config_manager import ConfigError

        auth = _make_auth(effective_role=MobileRole.MOBILE_READONLY)
        updates = {"daemon": {"log_level": "DEBUG"}}

        mock_manager = MagicMock()
        mock_manager.update_config.side_effect = ConfigError("Configuration changes require operator role")

        proxy = _make_proxy()

        with patch("elle.mobile.config_manager.get_config_manager", return_value=mock_manager):
            with pytest.raises(ProxyError) as exc_info:
                await proxy.proxy_update_config(auth, updates)

        assert exc_info.value.status_code == 400
        assert "operator role" in str(exc_info.value)
