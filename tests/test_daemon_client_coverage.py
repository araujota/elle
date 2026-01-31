"""Comprehensive tests for elle.cli.daemon_client to increase statement coverage.

Targets: DaemonClient init, _load_session_token, _get_headers, _get_client,
close, is_daemon_available, get_docker_state, get_network_state,
get_firewall_state, get_listeners, refresh_state, trigger_manvault_reindex,
get_manvault_status, search_manvault, search_incidents, fallback methods,
_read_session_token, get_daemon_client, and response models.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from elle.cli.daemon_client import (
    ContainerInfo,
    DaemonClient,
    DockerState,
    FirewallRule,
    FirewallState,
    InterfaceInfo,
    Listener,
    NetworkState,
    _read_session_token,
    get_daemon_client,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


# ---------------------------------------------------------------------------
# Response Models
# ---------------------------------------------------------------------------


class TestResponseModels:
    def test_container_info(self) -> None:
        c = ContainerInfo(
            id="abc123",
            names="mycontainer",
            image="nginx:latest",
            status="Up 5 hours",
            state="running",
            ports="80/tcp",
        )
        assert c.id == "abc123"
        assert c.state == "running"

    def test_docker_state_defaults(self) -> None:
        ds = DockerState()
        assert ds.docker_available is False
        assert ds.running_containers == ()
        assert ds.swarm_active is False

    def test_interface_info(self) -> None:
        i = InterfaceInfo(name="eth0", state="UP", addresses=("10.0.0.1",))
        assert i.name == "eth0"

    def test_firewall_rule(self) -> None:
        r = FirewallRule(action="ALLOW", direction="in", port="22", proto="tcp")
        assert r.action == "ALLOW"

    def test_firewall_state_defaults(self) -> None:
        fs = FirewallState()
        assert fs.active is False
        assert fs.backend == "unknown"

    def test_network_state_defaults(self) -> None:
        ns = NetworkState()
        assert ns.interfaces == ()
        assert ns.hostname == ""

    def test_listener(self) -> None:
        l = Listener(port=80, proto="tcp", address="0.0.0.0")
        assert l.port == 80
        assert l.is_wildcard is False


# ---------------------------------------------------------------------------
# _read_session_token
# ---------------------------------------------------------------------------


class TestReadSessionToken:
    def test_via_module(self) -> None:
        token_mod = MagicMock()
        token_mod.read_token_file.return_value = "abc123"
        with patch.dict("sys.modules", {"elle.daemon.api.session_token": token_mod}):
            result = _read_session_token()
        assert result == "abc123"

    def test_import_error_fallback_finds_file(self, tmp_path: Path) -> None:
        token_file = tmp_path / "session.token"
        token_file.write_text("fallback_tok")

        # Force ImportError for the module import
        with (
            patch.dict(
                "sys.modules",
                {"elle.daemon.api.session_token": None, "elle.daemon.api": None, "elle.daemon": None},
            ),
            patch("os.environ.get", return_value=str(tmp_path)),
            patch("os.getuid", return_value=1000),
        ):
            # The function tries several paths; we'll patch Path to return our file
            # Actually, it checks XDG_RUNTIME_DIR / elle / session.token
            # We need the *elle* subdirectory
            elle_dir = tmp_path / "elle"
            elle_dir.mkdir()
            real_token = elle_dir / "session.token"
            real_token.write_text("xdg_tok")

            result = _read_session_token()
        assert result == "xdg_tok"

    def test_import_error_no_file(self, tmp_path: Path) -> None:
        with (
            patch.dict(
                "sys.modules",
                {"elle.daemon.api.session_token": None, "elle.daemon.api": None, "elle.daemon": None},
            ),
            patch("os.environ.get", return_value=str(tmp_path / "nonexistent")),
            patch("os.getuid", return_value=1000),
        ):
            result = _read_session_token()
        assert result is None

    def test_general_exception(self) -> None:
        token_mod = MagicMock()
        token_mod.read_token_file.side_effect = RuntimeError("oops")
        mod = MagicMock()
        mod.read_token_file = token_mod.read_token_file
        # Simulate the outer except Exception catching RuntimeError
        with patch.dict("sys.modules", {"elle.daemon.api.session_token": mod}):
            result = _read_session_token()
        assert result is None


# ---------------------------------------------------------------------------
# DaemonClient init and basic methods
# ---------------------------------------------------------------------------


class TestDaemonClientInit:
    def test_defaults(self) -> None:
        client = DaemonClient()
        assert client._base_url == "http://127.0.0.1:8377"
        assert client._timeout == 10.0
        assert client._client is None
        assert client._daemon_available is None

    def test_custom_params(self) -> None:
        client = DaemonClient(base_url="http://localhost:9999", timeout=5.0)
        assert client._base_url == "http://localhost:9999"
        assert client._timeout == 5.0


class TestSessionTokenLoading:
    def test_load_once_cached(self) -> None:
        client = DaemonClient()
        with patch("elle.cli.daemon_client._read_session_token", return_value="tok"):
            token = client._load_session_token()
        assert token == "tok"
        # Second call should use cache
        token2 = client._load_session_token()
        assert token2 == "tok"

    def test_load_no_token(self) -> None:
        client = DaemonClient()
        with patch("elle.cli.daemon_client._read_session_token", return_value=None):
            token = client._load_session_token()
        assert token is None

    def test_get_headers_with_token(self) -> None:
        client = DaemonClient()
        client._token_loaded = True
        client._session_token = "test-token"
        headers = client._get_headers()
        assert headers["X-Elle-Token"] == "test-token"

    def test_get_headers_no_token(self) -> None:
        client = DaemonClient()
        client._token_loaded = True
        client._session_token = None
        headers = client._get_headers()
        assert "X-Elle-Token" not in headers


# ---------------------------------------------------------------------------
# Async methods
# ---------------------------------------------------------------------------


class TestDaemonClientAsync:
    @pytest.mark.asyncio
    async def test_get_client_creates_once(self) -> None:
        client = DaemonClient()
        client._token_loaded = True
        client._session_token = None
        http_client = await client._get_client()
        assert http_client is not None
        # Second call returns same
        http_client2 = await client._get_client()
        assert http_client2 is http_client
        await client.close()

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        client = DaemonClient()
        client._token_loaded = True
        client._session_token = "tok"
        client._daemon_available = True
        await client._get_client()
        await client.close()
        assert client._client is None
        assert client._token_loaded is False
        assert client._session_token is None
        assert client._daemon_available is None

    @pytest.mark.asyncio
    async def test_close_when_no_client(self) -> None:
        client = DaemonClient()
        await client.close()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_is_daemon_available_true(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_mock_response(200))
        client._client = mock_http
        result = await client.is_daemon_available()
        assert result is True
        assert client._daemon_available is True

    @pytest.mark.asyncio
    async def test_is_daemon_available_false_status(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_mock_response(500))
        client._client = mock_http
        result = await client.is_daemon_available()
        assert result is False

    @pytest.mark.asyncio
    async def test_is_daemon_available_exception(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        client._client = mock_http
        result = await client.is_daemon_available()
        assert result is False
        assert client._daemon_available is False


# ---------------------------------------------------------------------------
# get_docker_state
# ---------------------------------------------------------------------------


class TestGetDockerState:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        json_data = {
            "docker_available": True,
            "running_containers": [
                {
                    "id": "abc",
                    "names": "web",
                    "image": "nginx",
                    "status": "Up",
                    "state": "running",
                    "ports": "80",
                }
            ],
            "all_containers": [],
            "images": ["nginx:latest"],
            "networks": ["bridge"],
            "volumes": ["vol1"],
            "compose_services": [],
            "swarm_active": False,
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
        mock_http.get = AsyncMock(return_value=_mock_response(200, json_data))
        client._client = mock_http
        state = await client.get_docker_state()
        assert state.docker_available is True
        assert len(state.running_containers) == 1
        assert state.images == ("nginx:latest",)

    @pytest.mark.asyncio
    async def test_non_200_falls_back(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_mock_response(500))
        client._client = mock_http

        with patch.object(
            client,
            "_get_docker_state_fallback",
            new_callable=AsyncMock,
            return_value=DockerState(docker_available=False),
        ):
            state = await client.get_docker_state()
        assert state.docker_available is False

    @pytest.mark.asyncio
    async def test_exception_falls_back(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("fail"))
        client._client = mock_http

        with patch.object(
            client,
            "_get_docker_state_fallback",
            new_callable=AsyncMock,
            return_value=DockerState(docker_available=False),
        ):
            state = await client.get_docker_state()
        assert state.docker_available is False


# ---------------------------------------------------------------------------
# get_network_state
# ---------------------------------------------------------------------------


class TestGetNetworkState:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        json_data = {
            "interfaces": [{"name": "eth0", "state": "UP", "addresses": ["10.0.0.1"], "mac": "aa:bb", "mtu": 1500}],
            "default_gateway": "10.0.0.1",
            "default_interface": "eth0",
            "dns_servers": ["8.8.8.8"],
            "hostname": "testhost",
            "wireguard_interfaces": [],
            "firewall_active": True,
            "firewall_backend": "ufw",
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
        mock_http.get = AsyncMock(return_value=_mock_response(200, json_data))
        client._client = mock_http
        state = await client.get_network_state()
        assert state.hostname == "testhost"
        assert len(state.interfaces) == 1

    @pytest.mark.asyncio
    async def test_non_200(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_mock_response(503))
        client._client = mock_http

        with patch.object(
            client,
            "_get_network_state_fallback",
            new_callable=AsyncMock,
            return_value=NetworkState(),
        ):
            state = await client.get_network_state()
        assert state.hostname == ""

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("fail"))
        client._client = mock_http

        with patch.object(
            client,
            "_get_network_state_fallback",
            new_callable=AsyncMock,
            return_value=NetworkState(),
        ):
            state = await client.get_network_state()
        assert state.hostname == ""


# ---------------------------------------------------------------------------
# get_firewall_state
# ---------------------------------------------------------------------------


class TestGetFirewallState:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        json_data = {
            "active": True,
            "backend": "ufw",
            "default_incoming": "deny",
            "default_outgoing": "allow",
            "rules": [{"action": "ALLOW", "direction": "in", "port": "22", "proto": "tcp"}],
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
        mock_http.get = AsyncMock(return_value=_mock_response(200, json_data))
        client._client = mock_http
        state = await client.get_firewall_state()
        assert state.active is True
        assert len(state.rules) == 1

    @pytest.mark.asyncio
    async def test_non_200(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_mock_response(500))
        client._client = mock_http
        state = await client.get_firewall_state()
        assert state.active is False

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("fail"))
        client._client = mock_http
        state = await client.get_firewall_state()
        assert state.active is False


# ---------------------------------------------------------------------------
# get_listeners
# ---------------------------------------------------------------------------


class TestGetListeners:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        json_data = {"listeners": [{"port": 80, "proto": "tcp", "address": "0.0.0.0", "pid": 123, "process": "nginx"}]}
        mock_http.get = AsyncMock(return_value=_mock_response(200, json_data))
        client._client = mock_http
        listeners = await client.get_listeners()
        assert len(listeners) == 1
        assert listeners[0].port == 80

    @pytest.mark.asyncio
    async def test_non_200(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_mock_response(500))
        client._client = mock_http
        listeners = await client.get_listeners()
        assert listeners == ()

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("fail"))
        client._client = mock_http
        listeners = await client.get_listeners()
        assert listeners == ()


# ---------------------------------------------------------------------------
# refresh_state
# ---------------------------------------------------------------------------


class TestRefreshState:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=_mock_response(200))
        client._client = mock_http
        result = await client.refresh_state()
        assert result is True

    @pytest.mark.asyncio
    async def test_non_200(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=_mock_response(500))
        client._client = mock_http
        result = await client.refresh_state()
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=Exception("fail"))
        client._client = mock_http
        result = await client.refresh_state()
        assert result is False


# ---------------------------------------------------------------------------
# trigger_manvault_reindex
# ---------------------------------------------------------------------------


class TestTriggerReindex:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=_mock_response(200, {"message": "Reindex started"}))
        client._client = mock_http
        ok, msg = await client.trigger_manvault_reindex()
        assert ok is True
        assert "Reindex" in msg

    @pytest.mark.asyncio
    async def test_non_200(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=_mock_response(500))
        client._client = mock_http
        ok, msg = await client.trigger_manvault_reindex()
        assert ok is False
        assert "500" in msg

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=Exception("boom"))
        client._client = mock_http
        ok, msg = await client.trigger_manvault_reindex()
        assert ok is False
        assert "boom" in msg


# ---------------------------------------------------------------------------
# get_manvault_status
# ---------------------------------------------------------------------------


class TestGetManvaultStatus:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_mock_response(200, {"total_docs": 100}))
        client._client = mock_http
        status = await client.get_manvault_status()
        assert status["total_docs"] == 100

    @pytest.mark.asyncio
    async def test_non_200(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_mock_response(500))
        client._client = mock_http
        status = await client.get_manvault_status()
        assert "error" in status

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("fail"))
        client._client = mock_http
        status = await client.get_manvault_status()
        assert "error" in status


# ---------------------------------------------------------------------------
# search_manvault
# ---------------------------------------------------------------------------


class TestSearchManvault:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_mock_response(200, {"results": [{"title": "test"}]}))
        client._client = mock_http
        results = await client.search_manvault("test query")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_non_200(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_mock_response(404))
        client._client = mock_http
        results = await client.search_manvault("test query")
        assert results == []

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("fail"))
        client._client = mock_http
        results = await client.search_manvault("test query")
        assert results == []


# ---------------------------------------------------------------------------
# search_incidents
# ---------------------------------------------------------------------------


class TestSearchIncidents:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_mock_response(200, {"results": [{"id": "inc1"}]}))
        client._client = mock_http
        results = await client.search_incidents("error", domain="network")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_no_domain(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_mock_response(200, {"results": []}))
        client._client = mock_http
        results = await client.search_incidents("query")
        assert results == []

    @pytest.mark.asyncio
    async def test_non_200(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=_mock_response(500))
        client._client = mock_http
        results = await client.search_incidents("query")
        assert results == []

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        client = DaemonClient()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("fail"))
        client._client = mock_http
        results = await client.search_incidents("query")
        assert results == []


# ---------------------------------------------------------------------------
# Docker fallback
# ---------------------------------------------------------------------------


class TestDockerFallback:
    @pytest.mark.asyncio
    async def test_docker_not_found(self) -> None:
        client = DaemonClient()
        with patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=FileNotFoundError):
            state = await client._get_docker_state_fallback()
        assert state.docker_available is False

    @pytest.mark.asyncio
    async def test_docker_timeout(self) -> None:
        client = DaemonClient()
        with patch(
            "asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=subprocess.TimeoutExpired("docker", 5),
        ):
            state = await client._get_docker_state_fallback()
        assert state.docker_available is False

    @pytest.mark.asyncio
    async def test_docker_not_available(self) -> None:
        client = DaemonClient()
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=mock_result):
            state = await client._get_docker_state_fallback()
        assert state.docker_available is False

    @pytest.mark.asyncio
    async def test_docker_available_with_data(self) -> None:
        """Test when docker is available and returns container data."""
        import json

        client = DaemonClient()

        container_json = json.dumps(
            {
                "ID": "abc123456789",
                "Names": "web",
                "Image": "nginx",
                "Status": "Up",
                "State": "running",
                "Ports": "80/tcp",
            }
        )

        call_count = 0

        async def mock_to_thread(fn, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                # docker info
                mock_result.returncode = 0
            elif call_count == 2:
                # docker ps
                mock_result.returncode = 0
                mock_result.stdout = container_json
            elif call_count == 3:
                # docker images
                mock_result.returncode = 0
                mock_result.stdout = "nginx:latest\n<none>:<none>"
            elif call_count == 4:
                # docker network ls
                mock_result.returncode = 0
                mock_result.stdout = "bridge\nhost"
            elif call_count == 5:
                # docker volume ls
                mock_result.returncode = 0
                mock_result.stdout = "vol1"
            else:
                mock_result.returncode = 1
                mock_result.stdout = ""
            return mock_result

        with patch("asyncio.to_thread", side_effect=mock_to_thread):
            state = await client._get_docker_state_fallback()
        assert state.docker_available is True
        assert len(state.running_containers) == 1


# ---------------------------------------------------------------------------
# Network fallback
# ---------------------------------------------------------------------------


class TestNetworkFallback:
    @pytest.mark.asyncio
    async def test_file_not_found(self) -> None:
        client = DaemonClient()
        with patch("asyncio.to_thread", new_callable=AsyncMock, side_effect=FileNotFoundError):
            state = await client._get_network_state_fallback()
        assert state.hostname == ""

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        client = DaemonClient()
        with patch(
            "asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=subprocess.TimeoutExpired("ip", 5),
        ):
            state = await client._get_network_state_fallback()
        assert state.hostname == ""

    @pytest.mark.asyncio
    async def test_generic_exception(self) -> None:
        client = DaemonClient()
        with patch(
            "asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=RuntimeError("bad"),
        ):
            state = await client._get_network_state_fallback()
        assert state.hostname == ""


# ---------------------------------------------------------------------------
# get_daemon_client singleton
# ---------------------------------------------------------------------------


class TestGetDaemonClient:
    def test_singleton(self) -> None:
        import elle.cli.daemon_client as mod

        old = mod._client
        try:
            mod._client = None
            c1 = get_daemon_client()
            c2 = get_daemon_client()
            assert c1 is c2
            assert isinstance(c1, DaemonClient)
        finally:
            mod._client = old
