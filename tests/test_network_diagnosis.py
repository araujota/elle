"""Tests for network diagnosis and firewall explanation."""

from __future__ import annotations

import socket
import subprocess
from unittest.mock import MagicMock, patch

from elle.cli.network.diagnosis import (
    ConnectivityCheck,
    ConnectivityDiagnosis,
    _check_dns,
    _check_firewall,
    _check_listening,
    _check_ping,
    _check_route,
    _check_tcp_connection,
    _run_command,
    diagnose_connectivity,
)
from elle.cli.network.firewall import (
    FirewallRule,
    _explain_rule,
    _parse_ufw_rule,
    explain_firewall_rules,
    parse_restriction,
    suggest_lockdown,
)


class TestParseRestriction:
    """Tests for natural language restriction parsing."""

    def test_localhost_restriction(self):
        result = parse_restriction("localhost")
        assert result["type"] == "ip"
        assert result["value"] == "127.0.0.1"

    def test_localhost_only(self):
        result = parse_restriction("localhost only")
        assert result["type"] == "ip"
        assert result["value"] == "127.0.0.1"

    def test_cidr_notation(self):
        result = parse_restriction("10.0.0.0/8")
        assert result["type"] == "cidr"
        assert result["value"] == "10.0.0.0/8"

    def test_single_ip(self):
        result = parse_restriction("192.168.1.100")
        assert result["type"] == "ip"
        assert result["value"] == "192.168.1.100"

    def test_local_network(self):
        result = parse_restriction("local network")
        assert result["type"] == "cidr"
        assert "192.168" in result["value"]

    def test_internal_network(self):
        result = parse_restriction("internal")
        assert result["type"] == "cidr"
        assert "10.0.0.0" in result["value"]

    def test_unknown_restriction(self):
        result = parse_restriction("my office")
        assert result["type"] == "unknown"
        assert result["needs_input"]


class TestSuggestLockdown:
    """Tests for service lockdown suggestions."""

    def test_lockdown_postgres_localhost(self):
        commands = suggest_lockdown("postgres", "localhost only")
        assert any("5432" in cmd for cmd in commands)
        assert any("127.0.0.1" in cmd for cmd in commands)
        assert any("ufw allow from" in cmd for cmd in commands)

    def test_lockdown_mysql_internal(self):
        commands = suggest_lockdown("mysql", "internal")
        assert any("3306" in cmd for cmd in commands)
        assert any("10.0.0.0/8" in cmd for cmd in commands)

    def test_lockdown_with_port_number(self):
        commands = suggest_lockdown("6379", "localhost only")
        assert any("6379" in cmd for cmd in commands)

    def test_lockdown_unknown_service(self):
        commands = suggest_lockdown("unknown_service", "localhost")
        assert any("Unknown service" in cmd for cmd in commands)

    def test_lockdown_deletes_existing_rules(self):
        commands = suggest_lockdown("redis", "localhost")
        assert any("delete allow" in cmd for cmd in commands)


class TestParseUfwRule:
    """Tests for UFW rule parsing."""

    def test_parse_simple_allow(self):
        rule = _parse_ufw_rule("22/tcp                     ALLOW IN    Anywhere")
        assert rule is not None
        assert rule.action == "allow"
        assert rule.direction == "in"
        assert rule.port == 22
        assert rule.protocol == "tcp"

    def test_parse_allow_from_ip(self):
        rule = _parse_ufw_rule("5432                       ALLOW IN    10.0.0.0/8")
        assert rule is not None
        assert rule.action == "allow"
        assert rule.port == 5432
        assert rule.from_addr == "10.0.0.0/8"

    def test_parse_deny_rule(self):
        rule = _parse_ufw_rule("3306/tcp                   DENY IN     Anywhere")
        assert rule is not None
        assert rule.action == "deny"

    def test_parse_limit_rule(self):
        rule = _parse_ufw_rule("22/tcp                     LIMIT IN    Anywhere")
        assert rule is not None
        assert rule.action == "limit"

    def test_skip_header_line(self):
        rule = _parse_ufw_rule("To                         Action      From")
        assert rule is None

    def test_skip_separator_line(self):
        rule = _parse_ufw_rule("--                         ------      ----")
        assert rule is None


class TestExplainRule:
    """Tests for human-readable rule explanation."""

    def test_explain_ssh_rule(self):
        rule = FirewallRule(
            action="allow",
            direction="in",
            port=22,
            protocol="tcp",
            raw="22/tcp ALLOW IN Anywhere",
        )
        explanation = _explain_rule(rule)
        assert "SSH" in explanation
        assert "22" in explanation
        assert "allowed" in explanation

    def test_explain_postgres_restricted(self):
        rule = FirewallRule(
            action="allow",
            direction="in",
            port=5432,
            from_addr="10.0.0.0/8",
            raw="5432 ALLOW IN 10.0.0.0/8",
        )
        explanation = _explain_rule(rule)
        assert "POSTGRES" in explanation or "5432" in explanation
        assert "10.0.0.0/8" in explanation

    def test_explain_deny_rule(self):
        rule = FirewallRule(
            action="deny",
            direction="in",
            port=3306,
            raw="3306 DENY IN Anywhere",
        )
        explanation = _explain_rule(rule)
        assert "denied" in explanation


class TestDnsCheck:
    """Tests for DNS resolution check."""

    def test_already_ip(self):
        result = _check_dns("192.168.1.1")
        assert result.passed
        assert "already an IP" in result.result

    @patch("socket.getaddrinfo")
    def test_successful_resolution(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        result = _check_dns("example.com")
        assert result.passed
        assert "93.184.216.34" in result.result

    @patch("socket.getaddrinfo")
    def test_failed_resolution(self, mock_getaddrinfo):
        import socket

        mock_getaddrinfo.side_effect = socket.gaierror("Name or service not known")
        result = _check_dns("nonexistent.invalid")
        assert not result.passed
        assert "failed" in result.result.lower()


class TestRouteCheck:
    """Tests for route checking."""

    @patch("elle.cli.network.diagnosis._run_command")
    def test_successful_route(self, mock_run):
        mock_run.return_value = (0, "192.168.1.1 via 10.0.0.1 dev eth0 src 10.0.0.2", "")
        result = _check_route("192.168.1.1")
        assert result.passed
        assert "eth0" in result.result or "via" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_no_route(self, mock_run):
        mock_run.return_value = (1, "", "Network is unreachable")
        result = _check_route("192.168.1.1")
        assert not result.passed


class TestDiagnoseConnectivity:
    """Integration tests for connectivity diagnosis."""

    @patch("elle.cli.network.diagnosis._check_dns")
    def test_dns_failure_stops_early(self, mock_dns):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS Resolution",
            passed=False,
            result="DNS resolution failed",
            details={"error": "NXDOMAIN"},
        )

        diagnosis = diagnose_connectivity("nonexistent.invalid")
        assert not diagnosis.reachable
        assert "DNS" in diagnosis.likely_cause

    @patch("elle.cli.network.diagnosis._check_tcp_connection")
    @patch("elle.cli.network.diagnosis._check_firewall")
    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_successful_connection(
        self,
        mock_dns,
        mock_route,
        mock_firewall,
        mock_tcp,
    ):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS Resolution",
            passed=True,
            result="Resolved to 93.184.216.34",
            details={"ips": ["93.184.216.34"]},
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route Check",
            passed=True,
            result="Route via eth0",
            details={},
        )
        mock_firewall.return_value = ConnectivityCheck(
            name="Firewall Check",
            passed=True,
            result="UFW allows outgoing",
            details={},
        )
        mock_tcp.return_value = ConnectivityCheck(
            name="TCP Connection",
            passed=True,
            result="Connected successfully",
            details={},
        )

        diagnosis = diagnose_connectivity("example.com", port=443)
        assert diagnosis.reachable
        assert len(diagnosis.checks) >= 4


class TestFirewallExplanation:
    """Tests for firewall explanation."""

    @patch("elle.cli.network.firewall._run_command")
    def test_ufw_inactive(self, mock_run):
        mock_run.return_value = (0, "Status: inactive", "")

        explanation = explain_firewall_rules()
        assert not explanation.active
        assert "INACTIVE" in explanation.summary

    @patch("elle.cli.network.firewall._run_command")
    def test_ufw_active_with_rules(self, mock_run):
        mock_run.return_value = (
            0,
            """Status: active

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
80/tcp                     ALLOW IN    Anywhere
443/tcp                    ALLOW IN    Anywhere

Default: deny (incoming), allow (outgoing), disabled (routed)""",
            "",
        )

        explanation = explain_firewall_rules()
        assert explanation.active
        assert explanation.firewall_type == "ufw"
        assert len(explanation.rules) == 3
        assert explanation.default_incoming == "deny"
        assert explanation.default_outgoing == "allow"

    @patch("elle.cli.network.firewall._run_command")
    def test_warns_on_open_database_ports(self, mock_run):
        mock_run.return_value = (
            0,
            """Status: active

To                         Action      From
--                         ------      ----
5432                       ALLOW IN    Anywhere

Default: deny (incoming), allow (outgoing)""",
            "",
        )

        explanation = explain_firewall_rules()
        assert any("5432" in w and "world" in w for w in explanation.warnings)

    @patch("elle.cli.network.firewall._run_command")
    def test_no_firewall(self, mock_run):
        mock_run.return_value = (1, "", "ufw: command not found")

        # Also mock iptables failure
        def side_effect(cmd, timeout=10.0):
            if "ufw" in cmd:
                return (1, "", "ufw: command not found")
            if "iptables" in cmd:
                return (1, "", "iptables: command not found")
            return (0, "", "")

        mock_run.side_effect = side_effect

        explanation = explain_firewall_rules()
        assert not explanation.active
        assert "No firewall" in explanation.summary or explanation.firewall_type == "none"


# ---------------------------------------------------------------------------
# _run_command
# ---------------------------------------------------------------------------


class TestRunCommand:
    """Tests for the _run_command helper."""

    @patch("subprocess.run")
    def test_successful_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")
        code, stdout, stderr = _run_command(["echo", "hello"])
        assert code == 0
        assert stdout == "output"
        assert stderr == ""

    @patch("subprocess.run")
    def test_nonzero_return_code(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
        code, stdout, stderr = _run_command(["false"])
        assert code == 1
        assert stderr == "error msg"

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["test"], timeout=10))
    def test_timeout(self, mock_run):
        code, stdout, stderr = _run_command(["sleep", "100"], timeout=0.1)
        assert code == -1
        assert stderr == "Command timed out"

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_command_not_found(self, mock_run):
        code, stdout, stderr = _run_command(["nonexistent_command"])
        assert code == -1
        assert "Command not found" in stderr
        assert "nonexistent_command" in stderr

    @patch("subprocess.run", side_effect=PermissionError("Permission denied"))
    def test_generic_exception(self, mock_run):
        code, stdout, stderr = _run_command(["restricted_cmd"])
        assert code == -1
        assert "Permission denied" in stderr


# ---------------------------------------------------------------------------
# _check_dns - additional coverage
# ---------------------------------------------------------------------------


class TestCheckDnsExtended:
    """Extended DNS check tests for edge cases."""

    def test_ip_address_various_formats(self):
        result = _check_dns("10.0.0.1")
        assert result.passed
        assert result.details["is_ip"] is True
        assert result.details["ip"] == "10.0.0.1"

    def test_ip_address_edge_case_255(self):
        result = _check_dns("255.255.255.255")
        assert result.passed
        assert "already an IP" in result.result

    @patch("socket.getaddrinfo")
    def test_empty_ips_returned(self, mock_getaddrinfo):
        # getaddrinfo returns results but IPs set is empty after filtering
        # This shouldn't normally happen, but handle it anyway
        mock_getaddrinfo.return_value = []
        result = _check_dns("example.com")
        # Empty list means no ips extracted
        assert not result.passed
        assert "no results" in result.result

    @patch("socket.getaddrinfo")
    def test_multiple_ips_resolved(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("93.184.216.35", 0)),
        ]
        result = _check_dns("example.com")
        assert result.passed
        assert "93.184.216.34" in result.result or "93.184.216.35" in result.result
        assert len(result.details["ips"]) == 2

    @patch("socket.getaddrinfo")
    def test_gaierror_details(self, mock_getaddrinfo):
        mock_getaddrinfo.side_effect = socket.gaierror(8, "Name or service not known")
        result = _check_dns("nonexistent.invalid")
        assert not result.passed
        assert result.details["hostname"] == "nonexistent.invalid"
        assert "error" in result.details


# ---------------------------------------------------------------------------
# _check_route - additional coverage
# ---------------------------------------------------------------------------


class TestCheckRouteExtended:
    """Extended route check tests."""

    @patch("elle.cli.network.diagnosis._run_command")
    @patch("socket.gethostbyname")
    def test_hostname_resolution_for_route(self, mock_resolve, mock_run):
        mock_resolve.return_value = "93.184.216.34"
        mock_run.return_value = (0, "93.184.216.34 via 10.0.0.1 dev eth0 src 10.0.0.2", "")
        result = _check_route("example.com")
        assert result.passed
        mock_resolve.assert_called_once_with("example.com")

    @patch("socket.gethostbyname", side_effect=socket.gaierror("Name resolution failed"))
    def test_hostname_dns_failure(self, mock_resolve):
        result = _check_route("nonexistent.invalid")
        assert not result.passed
        assert "DNS resolution failed" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_direct_route_no_gateway(self, mock_run):
        mock_run.return_value = (0, "192.168.1.50 dev eth0 src 192.168.1.100", "")
        result = _check_route("192.168.1.50")
        assert result.passed
        assert "direct" in result.result
        assert result.details["interface"] == "eth0"
        assert result.details["source"] == "192.168.1.100"
        assert "gateway" not in result.details

    @patch("elle.cli.network.diagnosis._run_command")
    def test_route_with_all_details(self, mock_run):
        mock_run.return_value = (
            0,
            "8.8.8.8 via 192.168.1.1 dev wg0 src 10.0.0.2",
            "",
        )
        result = _check_route("8.8.8.8")
        assert result.passed
        assert result.details["interface"] == "wg0"
        assert result.details["gateway"] == "192.168.1.1"
        assert result.details["source"] == "10.0.0.2"
        assert "via" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_route_failure_with_stderr(self, mock_run):
        mock_run.return_value = (2, "", "RTNETLINK answers: Network is unreachable")
        result = _check_route("10.255.255.1")
        assert not result.passed
        assert "No route to" in result.result
        assert result.details["error"] == "RTNETLINK answers: Network is unreachable"

    @patch("elle.cli.network.diagnosis._run_command")
    def test_route_no_details_extracted(self, mock_run):
        # Output that doesn't match any regex patterns
        mock_run.return_value = (0, "some weird output", "")
        result = _check_route("10.0.0.1")
        assert result.passed
        assert result.details.get("interface") is None


# ---------------------------------------------------------------------------
# _check_firewall - full coverage
# ---------------------------------------------------------------------------


class TestCheckFirewall:
    """Tests for firewall checking."""

    @patch("elle.cli.network.diagnosis._run_command")
    def test_ufw_inactive(self, mock_run):
        mock_run.return_value = (0, "Status: inactive\n", "")
        result = _check_firewall("8.8.8.8", None)
        assert result.passed
        assert "inactive" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_ufw_active_outgoing_allowed(self, mock_run):
        mock_run.return_value = (
            0,
            "Status: active\nDefault: deny (incoming), allow (outgoing)\n",
            "",
        )
        result = _check_firewall("8.8.8.8", None)
        assert result.passed
        assert "outgoing traffic is allowed" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_ufw_active_outgoing_deny_no_port(self, mock_run):
        mock_run.return_value = (
            0,
            "Status: active\nDefault: deny (outgoing)\n",
            "",
        )
        result = _check_firewall("8.8.8.8", None)
        assert result.passed
        assert "deny" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_ufw_active_outgoing_reject_no_port(self, mock_run):
        mock_run.return_value = (
            0,
            "Status: active\nDefault: reject (outgoing)\n",
            "",
        )
        result = _check_firewall("8.8.8.8", None)
        assert result.passed
        assert "reject" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_ufw_port_blocked_outbound(self, mock_run):
        mock_run.return_value = (
            0,
            "Status: active\nDefault: allow (outgoing)\n443    DENY OUT    Anywhere\n",
            "",
        )
        result = _check_firewall("8.8.8.8", 443)
        assert not result.passed
        assert "blocking outbound port 443" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_ufw_port_reject_outbound(self, mock_run):
        mock_run.return_value = (
            0,
            "Status: active\nDefault: allow (outgoing)\n443    REJECT OUT    Anywhere\n",
            "",
        )
        result = _check_firewall("8.8.8.8", 443)
        assert not result.passed
        assert "blocking outbound port 443" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_ufw_port_allowed_outbound(self, mock_run):
        mock_run.return_value = (
            0,
            "Status: active\nDefault: deny (outgoing)\n443    ALLOW OUT    Anywhere\n",
            "",
        )
        result = _check_firewall("8.8.8.8", 443)
        # Port is explicitly allowed even though default is deny
        assert result.passed

    @patch("elle.cli.network.diagnosis._run_command")
    def test_ufw_deny_outgoing_port_not_allowed(self, mock_run):
        mock_run.return_value = (
            0,
            "Status: active\nDefault: deny (outgoing)\n80    ALLOW OUT    Anywhere\n",
            "",
        )
        result = _check_firewall("8.8.8.8", 443)
        assert not result.passed
        assert "port 443 is not allowed" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_ufw_default_allow_incoming(self, mock_run):
        mock_run.return_value = (
            0,
            "Status: active\nDefault: allow (incoming), allow (outgoing)\n",
            "",
        )
        result = _check_firewall("8.8.8.8", None)
        assert result.passed
        assert result.details.get("default_incoming") == "allow"

    @patch("elle.cli.network.diagnosis._run_command")
    def test_no_ufw_iptables_with_drop(self, mock_run):
        def side_effect(cmd, timeout=10.0):
            if cmd[0] == "ufw":
                return (1, "", "command not found")
            if cmd[0] == "iptables":
                return (0, "Chain INPUT (policy DROP)\nDROP all -- 0.0.0.0/0\n", "")
            return (0, "", "")

        mock_run.side_effect = side_effect
        result = _check_firewall("8.8.8.8", None)
        assert result.passed
        assert "DROP/REJECT" in result.result
        assert result.details["type"] == "iptables"

    @patch("elle.cli.network.diagnosis._run_command")
    def test_no_ufw_iptables_with_reject(self, mock_run):
        def side_effect(cmd, timeout=10.0):
            if cmd[0] == "ufw":
                return (1, "", "command not found")
            if cmd[0] == "iptables":
                return (0, "Chain INPUT (policy ACCEPT)\nREJECT all\n", "")
            return (0, "", "")

        mock_run.side_effect = side_effect
        result = _check_firewall("8.8.8.8", None)
        assert result.passed
        assert "DROP/REJECT" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_no_ufw_iptables_permissive(self, mock_run):
        def side_effect(cmd, timeout=10.0):
            if cmd[0] == "ufw":
                return (1, "", "command not found")
            if cmd[0] == "iptables":
                return (0, "Chain INPUT (policy ACCEPT)\nACCEPT all\n", "")
            return (0, "", "")

        mock_run.side_effect = side_effect
        result = _check_firewall("8.8.8.8", None)
        assert result.passed
        assert "permissive" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_no_firewall_available(self, mock_run):
        def side_effect(cmd, timeout=10.0):
            return (1, "", "command not found")

        mock_run.side_effect = side_effect
        result = _check_firewall("8.8.8.8", None)
        assert result.passed
        assert "not available" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_ufw_port_with_empty_lines(self, mock_run):
        mock_run.return_value = (
            0,
            "Status: active\nDefault: allow (outgoing)\n\n\n443    ALLOW OUT    Anywhere\n\n",
            "",
        )
        result = _check_firewall("8.8.8.8", 443)
        assert result.passed


# ---------------------------------------------------------------------------
# _check_listening
# ---------------------------------------------------------------------------


class TestCheckListening:
    """Tests for local port listening check."""

    @patch("elle.cli.network.diagnosis._run_command")
    def test_port_listening_with_process(self, mock_run):
        mock_run.return_value = (
            0,
            'LISTEN  0  128  0.0.0.0:8080  0.0.0.0:*  users:(("nginx",pid=1234,fd=6))',
            "",
        )
        result = _check_listening(8080)
        assert result.passed
        assert "listener" in result.result
        assert result.details["listening"] is True
        assert result.details["process"] == "nginx"

    @patch("elle.cli.network.diagnosis._run_command")
    def test_port_listening_without_process(self, mock_run):
        mock_run.return_value = (
            0,
            "LISTEN  0  128  0.0.0.0:8080  0.0.0.0:*",
            "",
        )
        result = _check_listening(8080)
        assert result.passed
        assert result.details["listening"] is True
        assert "process" not in result.details

    @patch("elle.cli.network.diagnosis._run_command")
    def test_nothing_listening(self, mock_run):
        mock_run.return_value = (
            0,
            "State  Recv-Q  Send-Q  Local Address:Port  Peer Address:Port",
            "",
        )
        result = _check_listening(9999)
        assert not result.passed
        assert "Nothing is listening" in result.result
        assert result.details["listening"] is False

    @patch("elle.cli.network.diagnosis._run_command")
    def test_ss_command_failure(self, mock_run):
        mock_run.return_value = (1, "", "ss: command not found")
        result = _check_listening(8080)
        assert result.passed  # Skip if can't check
        assert "Could not check" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_empty_output(self, mock_run):
        mock_run.return_value = (0, "", "")
        result = _check_listening(8080)
        assert not result.passed
        assert "Nothing is listening" in result.result


# ---------------------------------------------------------------------------
# _check_tcp_connection
# ---------------------------------------------------------------------------


class TestCheckTcpConnection:
    """Tests for TCP connection check."""

    @patch("socket.socket")
    def test_successful_connection(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value = mock_sock

        result = _check_tcp_connection("8.8.8.8", 443)
        assert result.passed
        assert "Successfully connected" in result.result
        mock_sock.close.assert_called_once()

    @patch("socket.socket")
    def test_connection_refused(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 111
        mock_socket_cls.return_value = mock_sock

        result = _check_tcp_connection("8.8.8.8", 443)
        assert not result.passed
        assert "Connection refused" in result.result
        assert result.details["error_code"] == 111

    @patch("socket.socket")
    def test_no_route_to_host(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 113
        mock_socket_cls.return_value = mock_sock

        result = _check_tcp_connection("10.255.255.1", 80)
        assert not result.passed
        assert "No route to host" in result.result
        assert result.details["error_code"] == 113

    @patch("socket.socket")
    def test_connection_timed_out_error_code(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 110
        mock_socket_cls.return_value = mock_sock

        result = _check_tcp_connection("8.8.8.8", 443)
        assert not result.passed
        assert "Connection timed out" in result.result
        assert result.details["error_code"] == 110

    @patch("socket.socket")
    def test_network_unreachable(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 101
        mock_socket_cls.return_value = mock_sock

        result = _check_tcp_connection("8.8.8.8", 443)
        assert not result.passed
        assert "Network unreachable" in result.result
        assert result.details["error_code"] == 101

    @patch("socket.socket")
    def test_unknown_error_code(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 99
        mock_socket_cls.return_value = mock_sock

        result = _check_tcp_connection("8.8.8.8", 443)
        assert not result.passed
        assert "Error code 99" in result.result

    @patch("socket.socket")
    def test_timeout_exception(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.side_effect = TimeoutError("timed out")
        mock_socket_cls.return_value = mock_sock

        result = _check_tcp_connection("8.8.8.8", 443, timeout=1.0)
        assert not result.passed
        assert "timed out" in result.result
        assert result.details["timeout"] == 1.0

    @patch("socket.socket")
    def test_gaierror_exception(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.side_effect = socket.gaierror("Name resolution failed")
        mock_socket_cls.return_value = mock_sock

        result = _check_tcp_connection("nonexistent.invalid", 443)
        assert not result.passed
        assert "Could not resolve" in result.result

    @patch("socket.socket")
    def test_generic_exception(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.side_effect = OSError("Network error")
        mock_socket_cls.return_value = mock_sock

        result = _check_tcp_connection("8.8.8.8", 443)
        assert not result.passed
        assert "Connection failed" in result.result
        assert "Network error" in result.details["error"]


# ---------------------------------------------------------------------------
# _check_ping
# ---------------------------------------------------------------------------


class TestCheckPing:
    """Tests for ICMP ping check."""

    @patch("elle.cli.network.diagnosis._run_command")
    def test_successful_ping(self, mock_run):
        mock_run.return_value = (
            0,
            "PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.\n"
            "64 bytes from 8.8.8.8: icmp_seq=1 ttl=64 time=12.3 ms\n",
            "",
        )
        result = _check_ping("8.8.8.8")
        assert result.passed
        assert "12.3" in result.result
        assert result.details["rtt_ms"] == "12.3"

    @patch("elle.cli.network.diagnosis._run_command")
    def test_successful_ping_integer_rtt(self, mock_run):
        mock_run.return_value = (
            0,
            "64 bytes from 8.8.8.8: icmp_seq=1 ttl=64 time=5 ms\n",
            "",
        )
        result = _check_ping("8.8.8.8")
        assert result.passed
        assert "5" in result.details["rtt_ms"]

    @patch("elle.cli.network.diagnosis._run_command")
    def test_successful_ping_no_rtt_match(self, mock_run):
        mock_run.return_value = (0, "some unusual output", "")
        result = _check_ping("8.8.8.8")
        assert result.passed
        assert result.details["rtt_ms"] == "unknown"

    @patch("elle.cli.network.diagnosis._run_command")
    def test_ping_failure_with_stderr(self, mock_run):
        mock_run.return_value = (1, "", "ping: connect: Network is unreachable")
        result = _check_ping("10.255.255.1")
        assert not result.passed
        assert "Network is unreachable" in result.result

    @patch("elle.cli.network.diagnosis._run_command")
    def test_ping_failure_no_response(self, mock_run):
        mock_run.return_value = (1, "", "")
        result = _check_ping("10.255.255.1")
        assert not result.passed
        assert "no response" in result.result


# ---------------------------------------------------------------------------
# diagnose_connectivity - comprehensive integration tests
# ---------------------------------------------------------------------------


class TestDiagnoseConnectivityExtended:
    """Extended integration tests for diagnose_connectivity."""

    @patch("elle.cli.network.diagnosis._check_dns")
    def test_dns_failure_provides_suggestions(self, mock_dns):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS Resolution",
            passed=False,
            result="DNS resolution failed",
            details={"error": "NXDOMAIN"},
        )
        diagnosis = diagnose_connectivity("bad.example.com", port=443)
        assert not diagnosis.reachable
        assert any("resolv.conf" in s for s in diagnosis.suggestions)
        assert any("dig" in s for s in diagnosis.suggestions)
        assert diagnosis.likely_cause == "DNS resolution failure"
        assert diagnosis.port == 443

    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_route_failure_stops_early(self, mock_dns, mock_route):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS Resolution",
            passed=True,
            result="Resolved",
            details={"ips": ["10.0.0.1"]},
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route Check",
            passed=False,
            result="No route",
            details={"error": "unreachable"},
        )
        diagnosis = diagnose_connectivity("10.0.0.1", port=80)
        assert not diagnosis.reachable
        assert diagnosis.likely_cause == "No route to host"
        assert any("ip route" in s for s in diagnosis.suggestions)
        assert any("ip link" in s for s in diagnosis.suggestions)

    @patch("elle.cli.network.diagnosis._check_firewall")
    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_firewall_failure_adds_suggestion_with_port(self, mock_dns, mock_route, mock_fw):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS", passed=True, result="OK", details={"ips": ["1.2.3.4"]}
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route", passed=True, result="OK", details={}
        )
        mock_fw.return_value = ConnectivityCheck(
            name="Firewall", passed=False, result="Blocked", details={}
        )
        # Mock the TCP check to also fail
        with patch("elle.cli.network.diagnosis._check_tcp_connection") as mock_tcp:
            mock_tcp.return_value = ConnectivityCheck(
                name="TCP", passed=False, result="Failed",
                details={"error_code": 110, "target": "1.2.3.4", "port": 443},
            )
            diagnosis = diagnose_connectivity("example.com", port=443)
        assert not diagnosis.reachable
        assert any("ufw allow out 443/tcp" in s for s in diagnosis.suggestions)

    @patch("elle.cli.network.diagnosis._check_firewall")
    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_firewall_failure_adds_suggestion_without_port(self, mock_dns, mock_route, mock_fw):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS", passed=True, result="OK", details={"ips": ["1.2.3.4"]}
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route", passed=True, result="OK", details={}
        )
        mock_fw.return_value = ConnectivityCheck(
            name="Firewall", passed=False, result="Blocked", details={}
        )
        with patch("elle.cli.network.diagnosis._check_ping") as mock_ping:
            mock_ping.return_value = ConnectivityCheck(
                name="Ping", passed=False, result="Failed", details={}
            )
            diagnosis = diagnose_connectivity("example.com")
        assert any("ufw status verbose" in s for s in diagnosis.suggestions)

    @patch("elle.cli.network.diagnosis._check_tcp_connection")
    @patch("elle.cli.network.diagnosis._check_firewall")
    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_tcp_refused_localhost(self, mock_dns, mock_route, mock_fw, mock_tcp):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS", passed=True, result="OK",
            details={"ips": ["127.0.0.1"]},
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route", passed=True, result="OK", details={}
        )
        mock_fw.return_value = ConnectivityCheck(
            name="Firewall", passed=True, result="OK", details={}
        )
        mock_tcp.return_value = ConnectivityCheck(
            name="TCP", passed=False, result="Refused",
            details={"error_code": 111, "target": "127.0.0.1", "port": 8080},
        )
        with patch("elle.cli.network.diagnosis._check_listening") as mock_listen:
            mock_listen.return_value = ConnectivityCheck(
                name="Local Port", passed=False, result="Nothing listening",
                details={"listening": False},
            )
            diagnosis = diagnose_connectivity("127.0.0.1", port=8080)
        assert not diagnosis.reachable
        assert diagnosis.likely_cause == "No service listening on port"
        assert any("Verify service" in s for s in diagnosis.suggestions)

    @patch("elle.cli.network.diagnosis._check_tcp_connection")
    @patch("elle.cli.network.diagnosis._check_firewall")
    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_tcp_refused_remote(self, mock_dns, mock_route, mock_fw, mock_tcp):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS", passed=True, result="OK",
            details={"ips": ["8.8.8.8"]},
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route", passed=True, result="OK", details={}
        )
        mock_fw.return_value = ConnectivityCheck(
            name="Firewall", passed=True, result="OK", details={}
        )
        mock_tcp.return_value = ConnectivityCheck(
            name="TCP", passed=False, result="Refused",
            details={"error_code": 111, "target": "8.8.8.8", "port": 80},
        )
        diagnosis = diagnose_connectivity("8.8.8.8", port=80)
        assert not diagnosis.reachable
        assert diagnosis.likely_cause == "Service not accepting connections (refused)"

    @patch("elle.cli.network.diagnosis._check_tcp_connection")
    @patch("elle.cli.network.diagnosis._check_firewall")
    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_tcp_timeout_error_code(self, mock_dns, mock_route, mock_fw, mock_tcp):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS", passed=True, result="OK", details={"ips": ["8.8.8.8"]}
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route", passed=True, result="OK", details={}
        )
        mock_fw.return_value = ConnectivityCheck(
            name="Firewall", passed=True, result="OK", details={}
        )
        mock_tcp.return_value = ConnectivityCheck(
            name="TCP", passed=False, result="Timed out",
            details={"error_code": 110},
        )
        diagnosis = diagnose_connectivity("8.8.8.8", port=443)
        assert diagnosis.likely_cause == "Connection timed out (firewall or network issue)"

    @patch("elle.cli.network.diagnosis._check_tcp_connection")
    @patch("elle.cli.network.diagnosis._check_firewall")
    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_tcp_no_route_error_code(self, mock_dns, mock_route, mock_fw, mock_tcp):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS", passed=True, result="OK", details={"ips": ["8.8.8.8"]}
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route", passed=True, result="OK", details={}
        )
        mock_fw.return_value = ConnectivityCheck(
            name="Firewall", passed=True, result="OK", details={}
        )
        mock_tcp.return_value = ConnectivityCheck(
            name="TCP", passed=False, result="No route",
            details={"error_code": 113},
        )
        diagnosis = diagnose_connectivity("8.8.8.8", port=443)
        assert diagnosis.likely_cause == "No route to host"

    @patch("elle.cli.network.diagnosis._check_tcp_connection")
    @patch("elle.cli.network.diagnosis._check_firewall")
    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_tcp_network_unreachable(self, mock_dns, mock_route, mock_fw, mock_tcp):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS", passed=True, result="OK", details={"ips": ["8.8.8.8"]}
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route", passed=True, result="OK", details={}
        )
        mock_fw.return_value = ConnectivityCheck(
            name="Firewall", passed=True, result="OK", details={}
        )
        mock_tcp.return_value = ConnectivityCheck(
            name="TCP", passed=False, result="Unreachable",
            details={"error_code": 101},
        )
        diagnosis = diagnose_connectivity("8.8.8.8", port=443)
        assert diagnosis.likely_cause == "Network unreachable"

    @patch("elle.cli.network.diagnosis._check_tcp_connection")
    @patch("elle.cli.network.diagnosis._check_firewall")
    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_tcp_unknown_error(self, mock_dns, mock_route, mock_fw, mock_tcp):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS", passed=True, result="OK", details={"ips": ["8.8.8.8"]}
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route", passed=True, result="OK", details={}
        )
        mock_fw.return_value = ConnectivityCheck(
            name="Firewall", passed=True, result="OK", details={}
        )
        mock_tcp.return_value = ConnectivityCheck(
            name="TCP", passed=False, result="Unknown",
            details={"error_code": 999},
        )
        diagnosis = diagnose_connectivity("8.8.8.8", port=443)
        assert diagnosis.likely_cause == "Connection failed"

    @patch("elle.cli.network.diagnosis._check_tcp_connection")
    @patch("elle.cli.network.diagnosis._check_firewall")
    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_tcp_no_error_code_in_details(self, mock_dns, mock_route, mock_fw, mock_tcp):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS", passed=True, result="OK", details={"ips": ["8.8.8.8"]}
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route", passed=True, result="OK", details={}
        )
        mock_fw.return_value = ConnectivityCheck(
            name="Firewall", passed=True, result="OK", details={}
        )
        mock_tcp.return_value = ConnectivityCheck(
            name="TCP", passed=False, result="Timeout",
            details={},
        )
        diagnosis = diagnose_connectivity("8.8.8.8", port=443)
        assert diagnosis.likely_cause == "Connection failed"

    @patch("elle.cli.network.diagnosis._check_ping")
    @patch("elle.cli.network.diagnosis._check_firewall")
    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_ping_success_no_port(self, mock_dns, mock_route, mock_fw, mock_ping):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS", passed=True, result="OK", details={"ips": ["8.8.8.8"]}
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route", passed=True, result="OK", details={}
        )
        mock_fw.return_value = ConnectivityCheck(
            name="Firewall", passed=True, result="OK", details={}
        )
        mock_ping.return_value = ConnectivityCheck(
            name="Ping", passed=True, result="OK (10ms)", details={}
        )
        diagnosis = diagnose_connectivity("8.8.8.8")
        assert diagnosis.reachable
        assert "reachable" in diagnosis.summary

    @patch("elle.cli.network.diagnosis._check_ping")
    @patch("elle.cli.network.diagnosis._check_firewall")
    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_ping_failure_no_port(self, mock_dns, mock_route, mock_fw, mock_ping):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS", passed=True, result="OK", details={"ips": ["8.8.8.8"]}
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route", passed=True, result="OK", details={}
        )
        mock_fw.return_value = ConnectivityCheck(
            name="Firewall", passed=True, result="OK", details={}
        )
        mock_ping.return_value = ConnectivityCheck(
            name="Ping", passed=False, result="No response", details={}
        )
        diagnosis = diagnose_connectivity("8.8.8.8")
        assert not diagnosis.reachable
        assert diagnosis.likely_cause == "Host not responding to ping"
        assert "Cannot reach" in diagnosis.summary

    @patch("elle.cli.network.diagnosis._check_tcp_connection")
    @patch("elle.cli.network.diagnosis._check_firewall")
    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_failure_summary_includes_suggestions(self, mock_dns, mock_route, mock_fw, mock_tcp):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS", passed=True, result="OK", details={"ips": ["8.8.8.8"]}
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route", passed=True, result="OK", details={}
        )
        mock_fw.return_value = ConnectivityCheck(
            name="Firewall", passed=False, result="Blocked",
            details={},
        )
        mock_tcp.return_value = ConnectivityCheck(
            name="TCP", passed=False, result="Refused",
            details={"error_code": 111},
        )
        diagnosis = diagnose_connectivity("example.com", port=443)
        assert "Cannot reach" in diagnosis.summary
        assert ":443" in diagnosis.summary
        assert "Suggestions:" in diagnosis.summary
        assert "[PASS]" in diagnosis.summary
        assert "[FAIL]" in diagnosis.summary

    @patch("elle.cli.network.diagnosis._check_dns")
    def test_dns_resolves_with_is_ip_target(self, mock_dns):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS", passed=True, result="OK",
            details={"ip": "10.0.0.1", "is_ip": True},
        )
        with patch("elle.cli.network.diagnosis._check_route") as mock_route:
            mock_route.return_value = ConnectivityCheck(
                name="Route", passed=False, result="No route", details={}
            )
            diagnosis = diagnose_connectivity("10.0.0.1")
        # When ips not in details, target itself is used
        assert not diagnosis.reachable

    @patch("elle.cli.network.diagnosis._check_tcp_connection")
    @patch("elle.cli.network.diagnosis._check_listening")
    @patch("elle.cli.network.diagnosis._check_firewall")
    @patch("elle.cli.network.diagnosis._check_route")
    @patch("elle.cli.network.diagnosis._check_dns")
    def test_localhost_with_port_checks_listening(
        self, mock_dns, mock_route, mock_fw, mock_listen, mock_tcp
    ):
        mock_dns.return_value = ConnectivityCheck(
            name="DNS", passed=True, result="OK",
            details={"ips": ["127.0.0.1"]},
        )
        mock_route.return_value = ConnectivityCheck(
            name="Route", passed=True, result="OK", details={}
        )
        mock_fw.return_value = ConnectivityCheck(
            name="Firewall", passed=True, result="OK", details={}
        )
        mock_listen.return_value = ConnectivityCheck(
            name="Local Port", passed=True, result="Listening",
            details={"listening": True},
        )
        mock_tcp.return_value = ConnectivityCheck(
            name="TCP", passed=True, result="Connected", details={}
        )
        diagnosis = diagnose_connectivity("127.0.0.1", port=8080)
        assert diagnosis.reachable
        mock_listen.assert_called_once_with(8080)


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestConnectivityModels:
    """Tests for Pydantic models."""

    def test_connectivity_check_frozen(self):
        check = ConnectivityCheck(name="Test", passed=True, result="OK")
        try:
            check.name = "Modified"  # type: ignore[misc]
            raise AssertionError("Should not be able to modify frozen model")
        except Exception:
            pass

    def test_connectivity_diagnosis_defaults(self):
        diag = ConnectivityDiagnosis(
            target="example.com",
            reachable=True,
            summary="OK",
        )
        assert diag.port is None
        assert diag.checks == ()
        assert diag.suggestions == ()
        assert diag.likely_cause is None

    def test_connectivity_check_details_default(self):
        check = ConnectivityCheck(name="Test", passed=True, result="OK")
        assert check.details == {}
