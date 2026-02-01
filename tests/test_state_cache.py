"""Tests for state cache (state_cache.py).

Covers Pydantic models, StateCache public API, refresh logic,
Docker/network/firewall state parsing, and listeners.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.telemetry.state_cache import (
    ContainerInfo,
    DockerState,
    FirewallRule,
    FirewallState,
    InterfaceInfo,
    Listener,
    NetworkState,
    StateCache,
    SystemState,
)

# ---------------------------------------------------------------------------
# Tests: Pydantic Models
# ---------------------------------------------------------------------------


class TestContainerInfo:
    def test_create(self):
        c = ContainerInfo(
            id="abc123",
            names="webapp",
            image="nginx:latest",
            status="Up 2 hours",
            state="running",
            ports="80/tcp",
            created="2025-06-15",
        )
        assert c.id == "abc123"
        assert c.names == "webapp"
        assert c.state == "running"

    def test_defaults(self):
        c = ContainerInfo(
            id="abc123",
            names="test",
            image="img:tag",
            status="Up",
            state="running",
        )
        assert c.ports == ""
        assert c.created == ""


class TestDockerState:
    def test_defaults(self):
        ds = DockerState()
        assert ds.docker_available is False
        assert ds.running_containers == ()
        assert ds.swarm_active is False

    def test_with_containers(self):
        c = ContainerInfo(id="abc", names="web", image="img", status="Up", state="running")
        ds = DockerState(docker_available=True, running_containers=(c,))
        assert len(ds.running_containers) == 1
        assert ds.docker_available is True


class TestInterfaceInfo:
    def test_create(self):
        iface = InterfaceInfo(
            name="eth0",
            state="UP",
            addresses=("192.168.1.10",),
            mac="00:11:22:33:44:55",
            mtu=1500,
        )
        assert iface.name == "eth0"
        assert iface.state == "UP"
        assert len(iface.addresses) == 1

    def test_defaults(self):
        iface = InterfaceInfo(name="eth0", state="UP")
        assert iface.addresses == ()
        assert iface.mac is None
        assert iface.mtu is None


class TestFirewallRule:
    def test_create(self):
        rule = FirewallRule(
            action="ALLOW",
            direction="in",
            port="22",
            proto="tcp",
            from_addr="any",
        )
        assert rule.action == "ALLOW"
        assert rule.port == "22"

    def test_defaults(self):
        rule = FirewallRule(action="DENY", direction="out")
        assert rule.proto == "any"
        assert rule.from_addr == "any"
        assert rule.to_addr == "any"
        assert rule.comment is None


class TestFirewallState:
    def test_defaults(self):
        fs = FirewallState()
        assert fs.active is False
        assert fs.backend == "unknown"
        assert fs.rules == ()

    def test_active_firewall(self):
        rule = FirewallRule(action="allow", direction="in", port="443")
        fs = FirewallState(active=True, backend="ufw", rules=(rule,))
        assert fs.active is True
        assert len(fs.rules) == 1


class TestNetworkState:
    def test_defaults(self):
        ns = NetworkState()
        assert ns.interfaces == ()
        assert ns.default_gateway is None
        assert ns.hostname == ""
        assert ns.wireguard_interfaces == ()


class TestListener:
    def test_create(self):
        ls = Listener(
            port=80,
            proto="tcp",
            address="0.0.0.0",
            pid=1234,
            process="nginx",
            is_wildcard=True,
        )
        assert ls.port == 80
        assert ls.is_wildcard is True

    def test_defaults(self):
        ls = Listener(port=443, proto="tcp", address="127.0.0.1")
        assert ls.pid is None
        assert ls.process == "unknown"
        assert ls.is_wildcard is False


class TestSystemState:
    def test_defaults(self):
        ss = SystemState()
        assert ss.docker.docker_available is False
        assert ss.listeners == ()


# ---------------------------------------------------------------------------
# Tests: StateCache initialization
# ---------------------------------------------------------------------------


class TestStateCacheInit:
    def test_default_init(self):
        cache = StateCache()
        assert cache._docker_refresh == 30
        assert cache._network_refresh == 60
        assert cache._running is False

    def test_custom_intervals(self):
        cache = StateCache(
            docker_refresh_sec=10,
            network_refresh_sec=20,
            firewall_refresh_sec=30,
        )
        assert cache._docker_refresh == 10
        assert cache._network_refresh == 20

    def test_initial_stats(self):
        cache = StateCache()
        stats = cache.stats
        assert stats["docker_refreshes"] == 0
        assert stats["network_refreshes"] == 0
        assert stats["errors"] == 0


# ---------------------------------------------------------------------------
# Tests: StateCache public API
# ---------------------------------------------------------------------------


class TestStateCachePublicAPI:
    def test_get_docker_state(self):
        cache = StateCache()
        ds = cache.get_docker_state()
        assert isinstance(ds, DockerState)
        assert ds.docker_available is False

    def test_get_network_state(self):
        cache = StateCache()
        ns = cache.get_network_state()
        assert isinstance(ns, NetworkState)

    def test_get_firewall_state(self):
        cache = StateCache()
        fs = cache.get_firewall_state()
        assert isinstance(fs, FirewallState)

    def test_get_listeners(self):
        cache = StateCache()
        listeners = cache.get_listeners()
        assert listeners == ()

    def test_get_system_state(self):
        cache = StateCache()
        ss = cache.get_system_state()
        assert isinstance(ss, SystemState)
        assert isinstance(ss.docker, DockerState)
        assert isinstance(ss.network, NetworkState)

    def test_is_running(self):
        cache = StateCache()
        assert cache.is_running is False

    def test_stats_returns_copy(self):
        cache = StateCache()
        stats = cache.stats
        stats["docker_refreshes"] = 999
        assert cache.stats["docker_refreshes"] == 0


# ---------------------------------------------------------------------------
# Tests: StateCache start/stop
# ---------------------------------------------------------------------------


class TestStateCacheStartStop:
    @pytest.mark.asyncio
    async def test_start_sets_running(self):
        cache = StateCache()
        with patch.object(cache, "_refresh_all", new_callable=AsyncMock):
            with patch("asyncio.create_task"):
                await cache.start()
                assert cache.is_running is True

    @pytest.mark.asyncio
    async def test_start_when_already_running(self):
        cache = StateCache()
        cache._running = True
        # Should not error, just return
        with patch.object(cache, "_refresh_all", new_callable=AsyncMock) as mock_refresh:
            await cache.start()
            mock_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop(self):
        cache = StateCache()
        cache._running = True
        await cache.stop()
        assert cache.is_running is False


# ---------------------------------------------------------------------------
# Tests: StateCache._refresh_docker_state
# ---------------------------------------------------------------------------


class TestRefreshDockerState:
    @pytest.mark.asyncio
    async def test_docker_not_available(self):
        cache = StateCache()

        failed_result = MagicMock()
        failed_result.returncode = 1
        failed_result.stdout = ""

        with patch("asyncio.to_thread", return_value=failed_result):
            await cache._refresh_docker_state()

        assert cache.get_docker_state().docker_available is False
        assert cache._stats["docker_refreshes"] == 1

    @pytest.mark.asyncio
    async def test_docker_not_installed(self):
        cache = StateCache()

        with patch("asyncio.to_thread", side_effect=FileNotFoundError):
            await cache._refresh_docker_state()

        assert cache.get_docker_state().docker_available is False

    @pytest.mark.asyncio
    async def test_docker_timeout(self):
        cache = StateCache()

        with patch("asyncio.to_thread", side_effect=subprocess.TimeoutExpired("docker", 5)):
            await cache._refresh_docker_state()

        assert cache._stats["errors"] == 1

    @pytest.mark.asyncio
    async def test_docker_generic_error(self):
        cache = StateCache()

        with patch("asyncio.to_thread", side_effect=RuntimeError("unexpected")):
            await cache._refresh_docker_state()

        assert cache._stats["errors"] == 1


# ---------------------------------------------------------------------------
# Tests: StateCache._get_containers
# ---------------------------------------------------------------------------


class TestGetContainers:
    @pytest.mark.asyncio
    async def test_parse_container_json(self):
        cache = StateCache()

        container_json = json.dumps(
            {
                "ID": "abc123def456",
                "Names": "webapp",
                "Image": "nginx:latest",
                "Status": "Up 2 hours",
                "State": "running",
                "Ports": "0.0.0.0:80->80/tcp",
                "CreatedAt": "2025-06-15",
            }
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = container_json

        with patch("asyncio.to_thread", return_value=mock_result):
            containers = await cache._get_containers()

        assert len(containers) == 1
        assert containers[0].names == "webapp"
        assert containers[0].image == "nginx:latest"
        assert containers[0].id == "abc123def456"[:12]

    @pytest.mark.asyncio
    async def test_parse_invalid_json(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not valid json"

        with patch("asyncio.to_thread", return_value=mock_result):
            containers = await cache._get_containers()

        assert containers == ()

    @pytest.mark.asyncio
    async def test_empty_output(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("asyncio.to_thread", return_value=mock_result):
            containers = await cache._get_containers()

        assert containers == ()

    @pytest.mark.asyncio
    async def test_failed_command(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("asyncio.to_thread", return_value=mock_result):
            containers = await cache._get_containers()

        assert containers == ()


# ---------------------------------------------------------------------------
# Tests: StateCache._get_docker_items
# ---------------------------------------------------------------------------


class TestGetDockerItems:
    @pytest.mark.asyncio
    async def test_get_items(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "item1\nitem2\nitem3"

        with patch("asyncio.to_thread", return_value=mock_result):
            items = await cache._get_docker_items(["docker", "images"])

        assert items == ["item1", "item2", "item3"]

    @pytest.mark.asyncio
    async def test_get_items_with_filter(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "good\n<none>:<none>\nalso_good"

        with patch("asyncio.to_thread", return_value=mock_result):
            items = await cache._get_docker_items(
                ["docker", "images"],
                filter_fn=lambda x: x and x != "<none>:<none>",
            )

        assert len(items) == 2
        assert "<none>:<none>" not in items

    @pytest.mark.asyncio
    async def test_get_items_failed(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("asyncio.to_thread", return_value=mock_result):
            items = await cache._get_docker_items(["docker", "images"])

        assert items == []


# ---------------------------------------------------------------------------
# Tests: StateCache._get_compose_services
# ---------------------------------------------------------------------------


class TestGetComposeServices:
    @pytest.mark.asyncio
    async def test_get_compose_services(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "web\ndb\nredis"

        with patch("asyncio.to_thread", return_value=mock_result):
            services = await cache._get_compose_services()

        assert services == ["web", "db", "redis"]

    @pytest.mark.asyncio
    async def test_compose_not_available(self):
        cache = StateCache()

        with patch("asyncio.to_thread", side_effect=FileNotFoundError):
            services = await cache._get_compose_services()

        assert services == []


# ---------------------------------------------------------------------------
# Tests: StateCache._check_swarm_active
# ---------------------------------------------------------------------------


class TestCheckSwarmActive:
    @pytest.mark.asyncio
    async def test_swarm_active(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "active"

        with patch("asyncio.to_thread", return_value=mock_result):
            result = await cache._check_swarm_active()

        assert result is True

    @pytest.mark.asyncio
    async def test_swarm_inactive(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("asyncio.to_thread", return_value=mock_result):
            result = await cache._check_swarm_active()

        assert result is False

    @pytest.mark.asyncio
    async def test_swarm_error(self):
        cache = StateCache()

        with patch("asyncio.to_thread", side_effect=FileNotFoundError):
            result = await cache._check_swarm_active()

        assert result is False


# ---------------------------------------------------------------------------
# Tests: StateCache._get_interfaces
# ---------------------------------------------------------------------------


class TestGetInterfaces:
    @pytest.mark.asyncio
    async def test_parse_interfaces(self):
        cache = StateCache()

        ip_json = json.dumps(
            [
                {
                    "ifname": "eth0",
                    "operstate": "UP",
                    "address": "00:11:22:33:44:55",
                    "mtu": 1500,
                    "addr_info": [
                        {"local": "192.168.1.10", "family": "inet"},
                    ],
                },
                {
                    "ifname": "lo",
                    "operstate": "UNKNOWN",
                    "addr_info": [{"local": "127.0.0.1"}],
                },
            ]
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ip_json

        with patch("asyncio.to_thread", return_value=mock_result):
            interfaces = await cache._get_interfaces()

        # lo should be filtered out
        assert len(interfaces) == 1
        assert interfaces[0].name == "eth0"
        assert interfaces[0].state == "UP"
        assert "192.168.1.10" in interfaces[0].addresses

    @pytest.mark.asyncio
    async def test_no_interfaces(self):
        cache = StateCache()

        with patch("asyncio.to_thread", side_effect=FileNotFoundError):
            interfaces = await cache._get_interfaces()

        assert interfaces == ()


# ---------------------------------------------------------------------------
# Tests: StateCache._get_default_route
# ---------------------------------------------------------------------------


class TestGetDefaultRoute:
    @pytest.mark.asyncio
    async def test_parse_default_route(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "default via 192.168.1.1 dev eth0 proto dhcp metric 100"

        with patch("asyncio.to_thread", return_value=mock_result):
            gateway, iface = await cache._get_default_route()

        assert gateway == "192.168.1.1"
        assert iface == "eth0"

    @pytest.mark.asyncio
    async def test_no_default_route(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("asyncio.to_thread", return_value=mock_result):
            gateway, iface = await cache._get_default_route()

        assert gateway is None
        assert iface is None

    @pytest.mark.asyncio
    async def test_ip_not_found(self):
        cache = StateCache()

        with patch("asyncio.to_thread", side_effect=FileNotFoundError):
            gateway, iface = await cache._get_default_route()

        assert gateway is None
        assert iface is None


# ---------------------------------------------------------------------------
# Tests: StateCache._get_dns_servers
# ---------------------------------------------------------------------------


class TestGetDnsServers:
    @pytest.mark.asyncio
    async def test_parse_resolv_conf(self, tmp_path):
        cache = StateCache()
        resolv_conf = tmp_path / "resolv.conf"
        resolv_conf.write_text("nameserver 8.8.8.8\nnameserver 8.8.4.4\nsearch example.com\n")

        with patch("elle.daemon.telemetry.state_cache.Path") as MockPath:
            MockPath.return_value.read_text.return_value = resolv_conf.read_text()
            servers = await cache._get_dns_servers()

        assert "8.8.8.8" in servers
        assert "8.8.4.4" in servers

    @pytest.mark.asyncio
    async def test_resolv_conf_missing(self):
        cache = StateCache()

        with patch("elle.daemon.telemetry.state_cache.Path") as MockPath:
            MockPath.return_value.read_text.side_effect = FileNotFoundError
            servers = await cache._get_dns_servers()

        assert servers == ()


# ---------------------------------------------------------------------------
# Tests: StateCache._get_hostname
# ---------------------------------------------------------------------------


class TestGetHostname:
    @pytest.mark.asyncio
    async def test_get_hostname(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "my-server\n"

        with patch("asyncio.to_thread", return_value=mock_result):
            hostname = await cache._get_hostname()

        assert hostname == "my-server"

    @pytest.mark.asyncio
    async def test_hostname_not_found(self):
        cache = StateCache()

        with patch("asyncio.to_thread", side_effect=FileNotFoundError):
            hostname = await cache._get_hostname()

        assert hostname == ""


# ---------------------------------------------------------------------------
# Tests: StateCache._get_wireguard_interfaces
# ---------------------------------------------------------------------------


class TestGetWireguardInterfaces:
    @pytest.mark.asyncio
    async def test_wireguard_interfaces(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "wg0 wg1"

        with patch("asyncio.to_thread", return_value=mock_result):
            wg = await cache._get_wireguard_interfaces()

        assert "wg0" in wg
        assert "wg1" in wg

    @pytest.mark.asyncio
    async def test_wireguard_not_installed(self):
        cache = StateCache()

        with patch("asyncio.to_thread", side_effect=FileNotFoundError):
            wg = await cache._get_wireguard_interfaces()

        assert wg == ()


# ---------------------------------------------------------------------------
# Tests: StateCache._parse_ufw_status
# ---------------------------------------------------------------------------


class TestParseUfwStatus:
    def test_active_with_rules(self):
        cache = StateCache()
        output = """Status: active

Default: deny (incoming), allow (outgoing), deny (routed)

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW       Anywhere
80/tcp                     ALLOW       Anywhere
443/tcp                    ALLOW       Anywhere
"""
        result = cache._parse_ufw_status(output)
        assert result.active is True
        assert result.backend == "ufw"
        assert result.default_incoming == "deny"
        assert result.default_outgoing == "allow"
        assert len(result.rules) > 0

    def test_inactive(self):
        cache = StateCache()
        output = "Status: inactive"
        result = cache._parse_ufw_status(output)
        assert result.active is False

    def test_deny_outgoing(self):
        cache = StateCache()
        output = """Status: active

Default: deny (incoming), deny (outgoing), deny (routed)
"""
        result = cache._parse_ufw_status(output)
        assert result.default_outgoing == "deny"


# ---------------------------------------------------------------------------
# Tests: StateCache._get_firewall_state
# ---------------------------------------------------------------------------


class TestGetFirewallState:
    @pytest.mark.asyncio
    async def test_ufw_available(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Status: active\nDefault: deny (incoming), allow (outgoing)"

        with patch("asyncio.to_thread", return_value=mock_result):
            fw = await cache._get_firewall_state()

        assert fw.active is True
        assert fw.backend == "ufw"

    @pytest.mark.asyncio
    async def test_iptables_fallback(self):
        cache = StateCache()

        call_count = 0

        async def mock_to_thread(func, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FileNotFoundError  # ufw not found
            # iptables result
            result = MagicMock()
            result.returncode = 0
            result.stdout = "Chain INPUT (policy DROP)\nTarget"
            return result

        with patch("asyncio.to_thread", side_effect=mock_to_thread):
            fw = await cache._get_firewall_state()

        assert fw.backend == "iptables"
        assert fw.active is True

    @pytest.mark.asyncio
    async def test_no_firewall(self):
        cache = StateCache()

        with patch("asyncio.to_thread", side_effect=FileNotFoundError):
            fw = await cache._get_firewall_state()

        assert fw.backend == "unknown"


# ---------------------------------------------------------------------------
# Tests: StateCache._get_listeners
# ---------------------------------------------------------------------------


class TestGetListeners:
    @pytest.mark.asyncio
    async def test_parse_ss_output(self):
        cache = StateCache()

        ss_output = """State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
LISTEN 0      128    0.0.0.0:80   0.0.0.0:*     users:(("nginx",pid=1234,fd=6))
LISTEN 0      128    127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=5678,fd=3))
"""

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ss_output

        with patch("asyncio.to_thread", return_value=mock_result):
            listeners = await cache._get_listeners()

        assert len(listeners) == 2
        # First listener: nginx on port 80
        assert listeners[0].port == 80
        assert listeners[0].process == "nginx"
        assert listeners[0].pid == 1234
        assert listeners[0].is_wildcard is True
        # Second listener: postgres on 127.0.0.1:5432
        assert listeners[1].port == 5432
        assert listeners[1].is_wildcard is False

    @pytest.mark.asyncio
    async def test_ss_not_found(self):
        cache = StateCache()

        with patch("asyncio.to_thread", side_effect=FileNotFoundError):
            listeners = await cache._get_listeners()

        assert listeners == ()

    @pytest.mark.asyncio
    async def test_ss_failed(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("asyncio.to_thread", return_value=mock_result):
            listeners = await cache._get_listeners()

        assert listeners == ()


# ---------------------------------------------------------------------------
# Tests: StateCache.refresh_now
# ---------------------------------------------------------------------------


class TestRefreshNow:
    @pytest.mark.asyncio
    async def test_refresh_now(self):
        cache = StateCache()

        with patch.object(cache, "_refresh_all", new_callable=AsyncMock) as mock_refresh:
            await cache.refresh_now()
            mock_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: StateCache._refresh_network_state
# ---------------------------------------------------------------------------


class TestRefreshNetworkState:
    @pytest.mark.asyncio
    async def test_refresh_increments_stats(self):
        cache = StateCache()

        with patch.object(cache, "_get_interfaces", new_callable=AsyncMock, return_value=()):
            with patch.object(cache, "_get_default_route", new_callable=AsyncMock, return_value=(None, None)):
                with patch.object(cache, "_get_dns_servers", new_callable=AsyncMock, return_value=()):
                    with patch.object(cache, "_get_hostname", new_callable=AsyncMock, return_value="test"):
                        with patch.object(cache, "_get_wireguard_interfaces", new_callable=AsyncMock, return_value=()):
                            with patch.object(
                                cache, "_get_firewall_state", new_callable=AsyncMock, return_value=FirewallState()
                            ):
                                with patch.object(cache, "_get_listeners", new_callable=AsyncMock, return_value=()):
                                    await cache._refresh_network_state()

        assert cache._stats["network_refreshes"] == 1
        assert cache.get_network_state().hostname == "test"

    @pytest.mark.asyncio
    async def test_refresh_network_error(self):
        cache = StateCache()

        with patch.object(cache, "_get_interfaces", new_callable=AsyncMock, side_effect=RuntimeError("fail")):
            await cache._refresh_network_state()

        assert cache._stats["errors"] == 1


# ---------------------------------------------------------------------------
# NEW Tests: Increase branch coverage to >90%
# ---------------------------------------------------------------------------


class TestRefreshAllActual:
    """Cover line 228: _refresh_all actually calling asyncio.gather."""

    @pytest.mark.asyncio
    async def test_refresh_all_calls_both_refreshes(self):
        cache = StateCache()
        with patch.object(cache, "_refresh_docker_state", new_callable=AsyncMock) as mock_d:
            with patch.object(cache, "_refresh_network_state", new_callable=AsyncMock) as mock_n:
                await cache._refresh_all()
                mock_d.assert_called_once()
                mock_n.assert_called_once()


class TestDockerRefreshLoop:
    """Cover lines 236-245: _docker_refresh_loop while loop body."""

    @pytest.mark.asyncio
    async def test_docker_refresh_loop_runs_and_stops(self):
        """Test the loop iterates once, then _running is set to False."""
        cache = StateCache()
        cache._running = True

        call_count = 0

        async def mock_refresh():
            nonlocal call_count
            call_count += 1
            cache._running = False  # Stop after first iteration

        with patch.object(cache, "_refresh_docker_state", side_effect=mock_refresh):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await cache._docker_refresh_loop()

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_docker_refresh_loop_cancelled(self):
        """Test CancelledError breaks the loop (line 241-242)."""
        cache = StateCache()
        cache._running = True

        with patch("asyncio.sleep", new_callable=AsyncMock, side_effect=asyncio.CancelledError):
            await cache._docker_refresh_loop()

        # Should exit cleanly without error increment
        assert cache._stats["errors"] == 0

    @pytest.mark.asyncio
    async def test_docker_refresh_loop_exception(self):
        """Test generic exception increments errors (lines 243-245)."""
        cache = StateCache()
        cache._running = True

        call_count = 0

        async def mock_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                cache._running = False
            raise RuntimeError("boom")

        with patch("asyncio.sleep", new_callable=AsyncMock, side_effect=mock_sleep):
            await cache._docker_refresh_loop()

        assert cache._stats["errors"] >= 1

    @pytest.mark.asyncio
    async def test_docker_refresh_loop_stopped_during_sleep(self):
        """Test that _running=False after sleep skips refresh (line 239 branch)."""
        cache = StateCache()
        cache._running = True

        async def mock_sleep(_):
            cache._running = False  # Stop during sleep

        with patch("asyncio.sleep", new_callable=AsyncMock, side_effect=mock_sleep):
            with patch.object(cache, "_refresh_docker_state", new_callable=AsyncMock) as mock_r:
                await cache._docker_refresh_loop()
                mock_r.assert_not_called()


class TestNetworkRefreshLoop:
    """Cover lines 249-258: _network_refresh_loop while loop body."""

    @pytest.mark.asyncio
    async def test_network_refresh_loop_runs_and_stops(self):
        cache = StateCache()
        cache._running = True

        call_count = 0

        async def mock_refresh():
            nonlocal call_count
            call_count += 1
            cache._running = False

        with patch.object(cache, "_refresh_network_state", side_effect=mock_refresh):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await cache._network_refresh_loop()

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_network_refresh_loop_cancelled(self):
        cache = StateCache()
        cache._running = True

        with patch("asyncio.sleep", new_callable=AsyncMock, side_effect=asyncio.CancelledError):
            await cache._network_refresh_loop()

        assert cache._stats["errors"] == 0

    @pytest.mark.asyncio
    async def test_network_refresh_loop_exception(self):
        cache = StateCache()
        cache._running = True

        call_count = 0

        async def mock_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                cache._running = False
            raise RuntimeError("boom")

        with patch("asyncio.sleep", new_callable=AsyncMock, side_effect=mock_sleep):
            await cache._network_refresh_loop()

        assert cache._stats["errors"] >= 1

    @pytest.mark.asyncio
    async def test_network_refresh_loop_stopped_during_sleep(self):
        """Branch: _running becomes False after sleep, so refresh is skipped."""
        cache = StateCache()
        cache._running = True

        async def mock_sleep(_):
            cache._running = False

        with patch("asyncio.sleep", new_callable=AsyncMock, side_effect=mock_sleep):
            with patch.object(cache, "_refresh_network_state", new_callable=AsyncMock) as mock_r:
                await cache._network_refresh_loop()
                mock_r.assert_not_called()


class TestRefreshDockerStateAvailable:
    """Cover lines 284-316: _refresh_docker_state when Docker IS available."""

    @pytest.mark.asyncio
    async def test_docker_available_full_refresh(self):
        cache = StateCache()

        docker_info_result = MagicMock()
        docker_info_result.returncode = 0
        docker_info_result.stdout = "Docker info"

        container = ContainerInfo(
            id="abc123def456",
            names="webapp",
            image="nginx:latest",
            status="Up 2 hours",
            state="running",
        )

        with patch("asyncio.to_thread", return_value=docker_info_result):
            with patch.object(
                cache,
                "_get_containers",
                new_callable=AsyncMock,
                return_value=(container,),
            ):
                with patch.object(
                    cache,
                    "_get_docker_items",
                    new_callable=AsyncMock,
                    return_value=["nginx:latest", "redis:7"],
                ):
                    with patch.object(
                        cache,
                        "_get_compose_services",
                        new_callable=AsyncMock,
                        return_value=["web", "db"],
                    ):
                        with patch.object(
                            cache,
                            "_check_swarm_active",
                            new_callable=AsyncMock,
                            return_value=False,
                        ):
                            await cache._refresh_docker_state()

        state = cache.get_docker_state()
        assert state.docker_available is True
        assert len(state.running_containers) == 1
        assert state.running_containers[0].names == "webapp"
        assert cache._last_docker_refresh is not None
        assert cache._stats["docker_refreshes"] == 1


class TestGetContainersAllFlag:
    """Cover line 334: _get_containers with all_containers=True inserts -a flag."""

    @pytest.mark.asyncio
    async def test_get_containers_with_all_flag(self):
        cache = StateCache()

        container_json = json.dumps(
            {
                "ID": "abc123def456",
                "Names": "webapp",
                "Image": "nginx:latest",
                "Status": "Exited (0)",
                "State": "exited",
                "Ports": "",
                "CreatedAt": "2025-06-15",
            }
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = container_json

        with patch("asyncio.to_thread", return_value=mock_result) as mock_thread:
            containers = await cache._get_containers(all_containers=True)

        assert len(containers) == 1
        # Verify the -a flag was included in the command
        call_args = mock_thread.call_args
        cmd_arg = call_args[0][1]  # second positional arg is the command list
        assert "-a" in cmd_arg


class TestGetComposeServicesFailed:
    """Cover branch 398->402: compose returns non-zero code."""

    @pytest.mark.asyncio
    async def test_compose_non_zero_returns_empty(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("asyncio.to_thread", return_value=mock_result):
            services = await cache._get_compose_services()

        assert services == []


class TestGetInterfacesNonZero:
    """Cover branch 473->495: ip addr returns non-zero."""

    @pytest.mark.asyncio
    async def test_interfaces_non_zero_returncode(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("asyncio.to_thread", return_value=mock_result):
            interfaces = await cache._get_interfaces()

        assert interfaces == ()


class TestGetDnsServersNameserverOnlyKeyword:
    """Cover branch 532->529: nameserver line with only one part (no IP)."""

    @pytest.mark.asyncio
    async def test_nameserver_line_without_ip(self):
        cache = StateCache()

        # "nameserver" alone (len(parts) == 1), should NOT append
        resolv_text = "nameserver\nnameserver 1.1.1.1\n"

        with patch("elle.daemon.telemetry.state_cache.Path") as MockPath:
            MockPath.return_value.read_text.return_value = resolv_text
            servers = await cache._get_dns_servers()

        # Only the valid one should be returned
        assert servers == ("1.1.1.1",)


class TestGetWireguardInterfacesNonZero:
    """Cover branch 563->568: wg show returns non-zero."""

    @pytest.mark.asyncio
    async def test_wireguard_non_zero_returncode(self):
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("asyncio.to_thread", return_value=mock_result):
            wg = await cache._get_wireguard_interfaces()

        assert wg == ()


class TestGetFirewallStateBranches:
    """Cover branches 582->589, 598->609: ufw non-zero falls to iptables,
    iptables non-zero falls to default."""

    @pytest.mark.asyncio
    async def test_ufw_nonzero_iptables_nonzero_returns_default(self):
        """Both ufw and iptables return non-zero, falls through to default."""
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 127  # command exists but fails
        mock_result.stdout = ""

        with patch("asyncio.to_thread", return_value=mock_result):
            fw = await cache._get_firewall_state()

        # Should return default FirewallState
        assert fw.backend == "unknown"
        assert fw.active is False

    @pytest.mark.asyncio
    async def test_ufw_nonzero_iptables_succeeds_no_drop(self):
        """UFW returns non-zero, iptables succeeds with no DROP/REJECT."""
        cache = StateCache()

        call_count = 0

        async def mock_to_thread(func, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # ufw fails
                result.returncode = 1
                result.stdout = ""
            else:
                # iptables succeeds with ACCEPT only
                result.returncode = 0
                result.stdout = "Chain INPUT (policy ACCEPT)\nACCEPT all"
            return result

        with patch("asyncio.to_thread", side_effect=mock_to_thread):
            fw = await cache._get_firewall_state()

        assert fw.backend == "iptables"
        assert fw.active is False  # no DROP or REJECT


class TestParseUfwStatusBranches:
    """Cover branches 621->623 and 629->618: reject (incoming/outgoing),
    and lines without ALLOW/DENY/REJECT that continue the loop."""

    def test_reject_incoming_policy(self):
        """Cover branch 621->623: reject (incoming) sets deny."""
        cache = StateCache()
        output = """Status: active

Default: reject (incoming), allow (outgoing), deny (routed)
"""
        result = cache._parse_ufw_status(output)
        assert result.active is True
        assert result.default_incoming == "deny"
        assert result.default_outgoing == "allow"

    def test_reject_outgoing_policy(self):
        """Cover the reject (outgoing) branch of line 623."""
        cache = StateCache()
        output = """Status: active

Default: allow (incoming), reject (outgoing), deny (routed)
"""
        result = cache._parse_ufw_status(output)
        assert result.default_incoming == "allow"
        assert result.default_outgoing == "deny"

    def test_lines_without_actions_continue(self):
        """Cover branch 629->618: lines that do not have ALLOW/DENY/REJECT."""
        cache = StateCache()
        output = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW       Anywhere
"""
        result = cache._parse_ufw_status(output)
        assert result.active is True
        # Only the ALLOW rule should be parsed
        assert len(result.rules) == 1
        assert result.rules[0].action == "allow"

    def test_deny_rule_parsed(self):
        """Cover DENY rule action branch in _parse_ufw_status."""
        cache = StateCache()
        output = """Status: active

Default: deny (incoming), allow (outgoing)

To                         Action      From
--                         ------      ----
8080/tcp                   DENY        Anywhere
"""
        result = cache._parse_ufw_status(output)
        assert len(result.rules) == 1
        assert result.rules[0].action == "deny"

    def test_reject_rule_parsed(self):
        """Cover REJECT rule action branch in _parse_ufw_status."""
        cache = StateCache()
        output = """Status: active

Default: deny (incoming), allow (outgoing)

To                         Action      From
--                         ------      ----
9090/tcp                   REJECT      Anywhere
"""
        result = cache._parse_ufw_status(output)
        assert len(result.rules) == 1
        assert result.rules[0].action == "reject"


class TestListenersIPv6:
    """Cover lines 669-670: IPv6 listener parsing with ']:' pattern."""

    @pytest.mark.asyncio
    async def test_parse_ipv6_listener(self):
        cache = StateCache()

        ss_output = (
            "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
            'LISTEN 0      128    [::]:80   [::]:*     users:(("nginx",pid=1234,fd=6))\n'
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ss_output

        with patch("asyncio.to_thread", return_value=mock_result):
            listeners = await cache._get_listeners()

        assert len(listeners) == 1
        assert listeners[0].port == 80
        assert listeners[0].address == "::"
        assert listeners[0].is_wildcard is True


class TestListenersNoProcessInfo:
    """Cover branch 680->689: listener with fewer than 6 parts (no process info)."""

    @pytest.mark.asyncio
    async def test_listener_without_process_info(self):
        cache = StateCache()

        ss_output = (
            "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
            "LISTEN 0      128    0.0.0.0:8080  0.0.0.0:*\n"
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ss_output

        with patch("asyncio.to_thread", return_value=mock_result):
            listeners = await cache._get_listeners()

        assert len(listeners) == 1
        assert listeners[0].port == 8080
        assert listeners[0].process == "unknown"
        assert listeners[0].pid is None


class TestListenersPidWithoutName:
    """Cover branches 684->686 and 686->689: pid found but name not found."""

    @pytest.mark.asyncio
    async def test_listener_pid_no_name(self):
        cache = StateCache()

        # Process info with pid= but no quoted name
        ss_output = (
            "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
            "LISTEN 0      128    0.0.0.0:3000  0.0.0.0:*     pid=9999\n"
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ss_output

        with patch("asyncio.to_thread", return_value=mock_result):
            listeners = await cache._get_listeners()

        assert len(listeners) == 1
        assert listeners[0].port == 3000
        assert listeners[0].pid == 9999
        assert listeners[0].process == "unknown"

    @pytest.mark.asyncio
    async def test_listener_name_no_pid(self):
        """Cover branch where name_match succeeds but pid_match fails."""
        cache = StateCache()

        ss_output = (
            "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
            'LISTEN 0      128    0.0.0.0:4000  0.0.0.0:*     users:(("myapp",fd=3))\n'
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ss_output

        with patch("asyncio.to_thread", return_value=mock_result):
            listeners = await cache._get_listeners()

        assert len(listeners) == 1
        assert listeners[0].port == 4000
        assert listeners[0].pid is None
        assert listeners[0].process == "myapp"


# ---------------------------------------------------------------------------
# NEW Coverage Tests: lines 228, 236-258, 284-316, 334, 669-670
# ---------------------------------------------------------------------------


class TestRefreshAllGather:
    """Cover line 228: _refresh_all calls asyncio.gather with both refresh methods."""

    @pytest.mark.asyncio
    async def test_refresh_all_gather_with_exceptions(self):
        """asyncio.gather with return_exceptions=True handles errors gracefully (line 228)."""
        cache = StateCache()

        async def fail_docker():
            raise RuntimeError("docker fail")

        async def fail_network():
            raise RuntimeError("network fail")

        with (
            patch.object(cache, "_refresh_docker_state", side_effect=fail_docker),
            patch.object(cache, "_refresh_network_state", side_effect=fail_network),
        ):
            # Should not raise because return_exceptions=True
            await cache._refresh_all()


class TestDockerRefreshLoopRunsRefresh:
    """Cover lines 236-240: _docker_refresh_loop executes _refresh_docker_state
    after sleep when _running is True."""

    @pytest.mark.asyncio
    async def test_docker_loop_calls_refresh_when_running(self):
        """After sleep, if _running is True, _refresh_docker_state is called (lines 239-240)."""
        cache = StateCache()
        cache._running = True

        call_count = 0

        async def tracked_refresh():
            nonlocal call_count
            call_count += 1
            cache._running = False  # Stop after one refresh

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch.object(cache, "_refresh_docker_state", side_effect=tracked_refresh),
        ):
            await cache._docker_refresh_loop()

        assert call_count == 1


class TestNetworkRefreshLoopRunsRefresh:
    """Cover lines 249-253: _network_refresh_loop executes _refresh_network_state
    after sleep when _running is True."""

    @pytest.mark.asyncio
    async def test_network_loop_calls_refresh_when_running(self):
        """After sleep, if _running is True, _refresh_network_state is called (lines 252-253)."""
        cache = StateCache()
        cache._running = True

        call_count = 0

        async def tracked_refresh():
            nonlocal call_count
            call_count += 1
            cache._running = False  # Stop after one refresh

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch.object(cache, "_refresh_network_state", side_effect=tracked_refresh),
        ):
            await cache._network_refresh_loop()

        assert call_count == 1


class TestDockerStateRefreshInternals:
    """Cover lines 284-316: Docker state refresh -- getting containers, images,
    networks, volumes, compose services, swarm, and setting _last_docker_refresh."""

    @pytest.mark.asyncio
    async def test_full_docker_refresh_sets_timestamp(self):
        """Full Docker refresh sets _last_docker_refresh timestamp (line 316)."""
        cache = StateCache()

        docker_info_result = MagicMock()
        docker_info_result.returncode = 0
        docker_info_result.stdout = "Docker info"

        with (
            patch("asyncio.to_thread", return_value=docker_info_result),
            patch.object(
                cache,
                "_get_containers",
                new_callable=AsyncMock,
                return_value=(),
            ),
            patch.object(
                cache,
                "_get_docker_items",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch.object(
                cache,
                "_get_compose_services",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch.object(
                cache,
                "_check_swarm_active",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            before = datetime.now(timezone.utc)
            await cache._refresh_docker_state()
            after = datetime.now(timezone.utc)

        assert cache._last_docker_refresh is not None
        assert before <= cache._last_docker_refresh <= after

    @pytest.mark.asyncio
    async def test_docker_not_available_sets_timestamp(self):
        """Docker not available still sets _last_docker_refresh (line 280)."""
        cache = StateCache()

        failed_result = MagicMock()
        failed_result.returncode = 1
        failed_result.stdout = ""

        with patch("asyncio.to_thread", return_value=failed_result):
            await cache._refresh_docker_state()

        assert cache._last_docker_refresh is not None
        assert cache.get_docker_state().docker_available is False


class TestContainerListTimeout:
    """Cover line 334: _get_containers timeout handling."""

    @pytest.mark.asyncio
    async def test_get_containers_timeout(self):
        """Container list command timing out is handled (line 334/342)."""
        cache = StateCache()

        with patch("asyncio.to_thread", side_effect=subprocess.TimeoutExpired("docker", 10)):
            # The timeout propagates; _refresh_docker_state catches it
            with pytest.raises(subprocess.TimeoutExpired):
                await cache._get_containers()

    @pytest.mark.asyncio
    async def test_get_containers_all_flag_inserts_a(self):
        """When all_containers=True, -a is inserted at index 2 (line 334)."""
        cache = StateCache()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("asyncio.to_thread", return_value=mock_result) as mock_thread:
            await cache._get_containers(all_containers=True)

        call_args = mock_thread.call_args
        cmd = call_args[0][1]
        assert "-a" in cmd
        assert cmd.index("-a") == 2


class TestListenerSsOutputWithProcessInfo:
    """Cover lines 669-670: ss output with full process info parsing."""

    @pytest.mark.asyncio
    async def test_listener_with_full_process_info(self):
        """Parse ss output with pid and process name (lines 680-687)."""
        cache = StateCache()

        ss_output = (
            "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
            'LISTEN 0      128    0.0.0.0:443   0.0.0.0:*     users:(("nginx",pid=1234,fd=6))\n'
            'LISTEN 0      128    0.0.0.0:3306  0.0.0.0:*     users:(("mysqld",pid=5678,fd=3))\n'
            'LISTEN 0      128    [::1]:6379    [::]:*         users:(("redis-server",pid=9012,fd=7))\n'
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ss_output

        with patch("asyncio.to_thread", return_value=mock_result):
            listeners = await cache._get_listeners()

        assert len(listeners) == 3

        # nginx on 443
        assert listeners[0].port == 443
        assert listeners[0].process == "nginx"
        assert listeners[0].pid == 1234
        assert listeners[0].is_wildcard is True

        # mysqld on 3306
        assert listeners[1].port == 3306
        assert listeners[1].process == "mysqld"
        assert listeners[1].pid == 5678

        # redis-server on IPv6 localhost:6379
        assert listeners[2].port == 6379
        assert listeners[2].process == "redis-server"
        assert listeners[2].pid == 9012

    @pytest.mark.asyncio
    async def test_listener_wildcard_star(self):
        """Parse ss output with * as wildcard address."""
        cache = StateCache()

        ss_output = (
            "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
            'LISTEN 0      128    *:8080  *:*     users:(("node",pid=4321,fd=10))\n'
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ss_output

        with patch("asyncio.to_thread", return_value=mock_result):
            listeners = await cache._get_listeners()

        assert len(listeners) == 1
        assert listeners[0].port == 8080
        assert listeners[0].is_wildcard is True
        assert listeners[0].process == "node"
        assert listeners[0].pid == 4321


class TestRefreshNetworkStateSetsTimestamp:
    """Cover line 454: _last_network_refresh is set after successful refresh."""

    @pytest.mark.asyncio
    async def test_network_refresh_sets_timestamp(self):
        cache = StateCache()

        with (
            patch.object(cache, "_get_interfaces", new_callable=AsyncMock, return_value=()),
            patch.object(cache, "_get_default_route", new_callable=AsyncMock, return_value=(None, None)),
            patch.object(cache, "_get_dns_servers", new_callable=AsyncMock, return_value=()),
            patch.object(cache, "_get_hostname", new_callable=AsyncMock, return_value="host1"),
            patch.object(cache, "_get_wireguard_interfaces", new_callable=AsyncMock, return_value=()),
            patch.object(cache, "_get_firewall_state", new_callable=AsyncMock, return_value=FirewallState()),
            patch.object(cache, "_get_listeners", new_callable=AsyncMock, return_value=()),
        ):
            before = datetime.now(timezone.utc)
            await cache._refresh_network_state()
            after = datetime.now(timezone.utc)

        assert cache._last_network_refresh is not None
        assert before <= cache._last_network_refresh <= after


class TestStartCreatesBackgroundTasks:
    """Cover lines 218-219: start() creates background tasks via asyncio.create_task."""

    @pytest.mark.asyncio
    async def test_start_creates_two_tasks(self):
        """start() calls asyncio.create_task twice (lines 218-219)."""
        cache = StateCache()

        with (
            patch.object(cache, "_refresh_all", new_callable=AsyncMock),
            patch("asyncio.create_task") as mock_create_task,
        ):
            await cache.start()

        assert mock_create_task.call_count == 2
