"""Tests for state cache (state_cache.py).

Covers Pydantic models, StateCache public API, refresh logic,
Docker/network/firewall state parsing, and listeners.
"""

from __future__ import annotations

import json
import subprocess
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
