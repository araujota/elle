"""Tests for network diagnosis and firewall explanation."""

from unittest.mock import patch

from elle.cli.network.diagnosis import (
    ConnectivityCheck,
    _check_dns,
    _check_route,
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
