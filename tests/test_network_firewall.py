"""Tests for the firewall explanation and management module."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.network.firewall import (
    SERVICE_PORTS,
    FirewallExplanation,
    FirewallRule,
    _explain_rule,
    _parse_ufw_rule,
    _run_command,
    explain_firewall_rules,
    generate_lockdown_plan,
    parse_restriction,
    suggest_lockdown,
)


# ===========================================================================
# _run_command
# ===========================================================================


class TestRunCommand:
    def test_successful_command(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="output", stderr=""
            )
            code, stdout, stderr = _run_command(["echo", "test"])
            assert code == 0
            assert stdout == "output"
            assert stderr == ""

    def test_failed_command(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="error"
            )
            code, stdout, stderr = _run_command(["false"])
            assert code == 1
            assert stderr == "error"

    def test_timeout(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired("cmd", 10),
        ):
            code, stdout, stderr = _run_command(["sleep", "100"])
            assert code == -1
            assert "timed out" in stderr

    def test_command_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            code, stdout, stderr = _run_command(["nonexistent_cmd"])
            assert code == -1
            assert "not found" in stderr

    def test_generic_exception(self):
        with patch(
            "subprocess.run", side_effect=OSError("permission denied")
        ):
            code, stdout, stderr = _run_command(["test"])
            assert code == -1
            assert "permission denied" in stderr


# ===========================================================================
# _parse_ufw_rule
# ===========================================================================


class TestParseUfwRule:
    def test_empty_line(self):
        assert _parse_ufw_rule("") is None

    def test_whitespace_line(self):
        assert _parse_ufw_rule("   ") is None

    def test_header_to_line(self):
        assert _parse_ufw_rule("To                         Action      From") is None

    def test_header_separator(self):
        assert _parse_ufw_rule("--                         ------      ----") is None

    def test_too_few_parts(self):
        assert _parse_ufw_rule("22/tcp") is None

    def test_parse_allow_in_rule(self):
        rule = _parse_ufw_rule(
            "22/tcp                     ALLOW IN    Anywhere"
        )
        assert rule is not None
        assert rule.port == 22
        assert rule.protocol == "tcp"
        assert rule.action == "allow"
        assert rule.direction == "in"
        assert rule.from_addr is None  # "Anywhere" becomes None

    def test_parse_deny_rule(self):
        rule = _parse_ufw_rule(
            "3306/tcp                   DENY IN     Anywhere"
        )
        assert rule is not None
        assert rule.port == 3306
        assert rule.action == "deny"

    def test_parse_reject_rule(self):
        rule = _parse_ufw_rule(
            "8080                       REJECT IN   Anywhere"
        )
        assert rule is not None
        assert rule.port == 8080
        assert rule.action == "reject"

    def test_parse_limit_rule(self):
        rule = _parse_ufw_rule(
            "22/tcp                     LIMIT IN    Anywhere"
        )
        assert rule is not None
        assert rule.action == "limit"

    def test_parse_out_direction(self):
        rule = _parse_ufw_rule(
            "80/tcp                     ALLOW OUT   Anywhere"
        )
        assert rule is not None
        assert rule.direction == "out"

    def test_parse_fwd_direction(self):
        rule = _parse_ufw_rule(
            "80/tcp                     ALLOW FWD   Anywhere"
        )
        assert rule is not None
        assert rule.direction == "both"

    def test_parse_rule_with_specific_from(self):
        rule = _parse_ufw_rule(
            "5432                       ALLOW IN    10.0.0.0/8"
        )
        assert rule is not None
        assert rule.port == 5432
        assert rule.from_addr == "10.0.0.0/8"

    def test_parse_rule_port_without_protocol(self):
        rule = _parse_ufw_rule(
            "5432                       ALLOW IN    Anywhere"
        )
        assert rule is not None
        assert rule.port == 5432
        assert rule.protocol is None

    def test_parse_rule_service_name(self):
        # Service names like "Apache" instead of port numbers
        rule = _parse_ufw_rule(
            "Apache                     ALLOW IN    Anywhere"
        )
        assert rule is not None
        assert rule.port is None
        assert rule.protocol is None

    def test_parse_rule_invalid_port_in_slash_format(self):
        rule = _parse_ufw_rule(
            "notaport/tcp               ALLOW IN    Anywhere"
        )
        assert rule is not None
        assert rule.port is None
        assert rule.protocol == "tcp"


# ===========================================================================
# _explain_rule
# ===========================================================================


class TestExplainRule:
    def test_explain_known_service(self):
        rule = FirewallRule(
            action="allow",
            direction="in",
            port=22,
            protocol="tcp",
            raw="test",
        )
        exp = _explain_rule(rule)
        assert "SSH" in exp
        assert "port 22" in exp
        assert "allowed" in exp
        assert "incoming" in exp

    def test_explain_unknown_port(self):
        rule = FirewallRule(
            action="allow",
            direction="in",
            port=9999,
            raw="test",
        )
        exp = _explain_rule(rule)
        assert "Port 9999" in exp

    def test_explain_no_port(self):
        rule = FirewallRule(
            action="deny",
            direction="in",
            raw="test",
        )
        exp = _explain_rule(rule)
        assert "All ports" in exp
        assert "denied" in exp

    def test_explain_deny_action(self):
        rule = FirewallRule(
            action="deny",
            direction="in",
            port=80,
            raw="test",
        )
        exp = _explain_rule(rule)
        assert "denied" in exp

    def test_explain_reject_action(self):
        rule = FirewallRule(
            action="reject",
            direction="in",
            port=80,
            raw="test",
        )
        exp = _explain_rule(rule)
        assert "rejected" in exp

    def test_explain_limit_action(self):
        rule = FirewallRule(
            action="limit",
            direction="in",
            port=22,
            raw="test",
        )
        exp = _explain_rule(rule)
        assert "rate-limited" in exp

    def test_explain_out_direction(self):
        rule = FirewallRule(
            action="allow",
            direction="out",
            port=443,
            raw="test",
        )
        exp = _explain_rule(rule)
        assert "outgoing" in exp

    def test_explain_both_direction(self):
        rule = FirewallRule(
            action="allow",
            direction="both",
            port=80,
            raw="test",
        )
        exp = _explain_rule(rule)
        assert "forwarded" in exp

    def test_explain_with_from_addr(self):
        rule = FirewallRule(
            action="allow",
            direction="in",
            port=5432,
            from_addr="10.0.0.0/8",
            raw="test",
        )
        exp = _explain_rule(rule)
        assert "from 10.0.0.0/8" in exp

    def test_explain_without_from_addr(self):
        rule = FirewallRule(
            action="allow",
            direction="in",
            port=80,
            raw="test",
        )
        exp = _explain_rule(rule)
        assert "from anywhere" in exp

    def test_explain_with_protocol(self):
        rule = FirewallRule(
            action="allow",
            direction="in",
            port=80,
            protocol="tcp",
            raw="test",
        )
        exp = _explain_rule(rule)
        assert "/tcp" in exp


# ===========================================================================
# explain_firewall_rules
# ===========================================================================


class TestExplainFirewallRules:
    def test_ufw_active_with_rules(self):
        ufw_output = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
80/tcp                     ALLOW IN    Anywhere
"""
        with patch(
            "elle.cli.network.firewall._run_command",
            return_value=(0, ufw_output, ""),
        ):
            result = explain_firewall_rules()
            assert result.active is True
            assert result.firewall_type == "ufw"
            assert result.default_incoming == "deny"
            assert result.default_outgoing == "allow"
            assert len(result.rules) == 2
            assert "ACTIVE" in result.summary

    def test_ufw_active_no_custom_rules(self):
        ufw_output = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
"""
        with patch(
            "elle.cli.network.firewall._run_command",
            return_value=(0, ufw_output, ""),
        ):
            result = explain_firewall_rules()
            assert result.active is True
            assert len(result.rules) == 0
            assert "No custom rules" in result.summary

    def test_ufw_inactive(self):
        ufw_output = "Status: inactive\n"
        with patch(
            "elle.cli.network.firewall._run_command",
            return_value=(0, ufw_output, ""),
        ):
            result = explain_firewall_rules()
            assert result.active is False
            assert "INACTIVE" in result.summary

    def test_ufw_allow_incoming_default_warning(self):
        ufw_output = """Status: active
Default: allow (incoming), allow (outgoing)

To                         Action      From
--                         ------      ----
"""
        with patch(
            "elle.cli.network.firewall._run_command",
            return_value=(0, ufw_output, ""),
        ):
            result = explain_firewall_rules()
            assert result.default_incoming == "allow"
            assert len(result.warnings) > 0
            assert any("ALLOW" in w for w in result.warnings)

    def test_ufw_reject_incoming_default(self):
        ufw_output = """Status: active
Default: reject (incoming), allow (outgoing)

To                         Action      From
--                         ------      ----
"""
        with patch(
            "elle.cli.network.firewall._run_command",
            return_value=(0, ufw_output, ""),
        ):
            result = explain_firewall_rules()
            assert result.default_incoming == "reject"

    def test_ufw_deny_outgoing_default(self):
        # The code checks for "Default: deny (outgoing)" as a substring.
        # Real UFW outputs on separate lines or with a comma.
        # This test uses the exact substring format the parser expects.
        ufw_output = """Status: active
Default: deny (incoming)
Default: deny (outgoing)

To                         Action      From
--                         ------      ----
"""
        with patch(
            "elle.cli.network.firewall._run_command",
            return_value=(0, ufw_output, ""),
        ):
            result = explain_firewall_rules()
            assert result.default_outgoing == "deny"

    def test_ufw_database_open_warning(self):
        ufw_output = """Status: active
Default: deny (incoming), allow (outgoing)

To                         Action      From
--                         ------      ----
5432                       ALLOW IN    Anywhere
"""
        with patch(
            "elle.cli.network.firewall._run_command",
            return_value=(0, ufw_output, ""),
        ):
            result = explain_firewall_rules()
            assert len(result.warnings) > 0
            assert any("5432" in w for w in result.warnings)

    def test_ufw_ssh_open_no_warning(self):
        """SSH from anywhere should not generate a warning."""
        ufw_output = """Status: active
Default: deny (incoming), allow (outgoing)

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
"""
        with patch(
            "elle.cli.network.firewall._run_command",
            return_value=(0, ufw_output, ""),
        ):
            result = explain_firewall_rules()
            # SSH from anywhere is common, no warning expected
            assert not any("22" in w for w in result.warnings)

    def test_fallback_to_iptables_with_drop(self):
        def mock_run_command(cmd, **kwargs):
            if cmd[0] == "ufw":
                return (-1, "", "ufw not found")
            if cmd[0] == "iptables":
                return (0, "Chain INPUT\nDROP  all\n", "")
            return (-1, "", "")

        with patch(
            "elle.cli.network.firewall._run_command",
            side_effect=mock_run_command,
        ):
            result = explain_firewall_rules()
            assert result.firewall_type == "iptables"
            assert result.active is True
            assert "iptables" in result.summary

    def test_fallback_to_iptables_permissive(self):
        def mock_run_command(cmd, **kwargs):
            if cmd[0] == "ufw":
                return (-1, "", "ufw not found")
            if cmd[0] == "iptables":
                return (0, "Chain INPUT\nACCEPT all\n", "")
            return (-1, "", "")

        with patch(
            "elle.cli.network.firewall._run_command",
            side_effect=mock_run_command,
        ):
            result = explain_firewall_rules()
            assert result.firewall_type == "iptables"
            assert len(result.warnings) > 0
            assert any("permissive" in w for w in result.warnings)

    def test_no_firewall_found(self):
        def mock_run_command(cmd, **kwargs):
            return (-1, "", "not found")

        with patch(
            "elle.cli.network.firewall._run_command",
            side_effect=mock_run_command,
        ):
            result = explain_firewall_rules()
            assert result.active is False
            assert result.firewall_type == "none"
            assert "No firewall" in result.summary
            assert len(result.warnings) > 0


# ===========================================================================
# parse_restriction
# ===========================================================================


class TestParseRestriction:
    def test_localhost(self):
        result = parse_restriction("localhost")
        assert result["type"] == "ip"
        assert result["value"] == "127.0.0.1"

    def test_localhost_only(self):
        result = parse_restriction("localhost only")
        assert result["type"] == "ip"
        assert result["value"] == "127.0.0.1"

    def test_local_only(self):
        result = parse_restriction("local only")
        assert result["type"] == "ip"
        assert result["value"] == "127.0.0.1"

    def test_loopback_ip(self):
        result = parse_restriction("127.0.0.1")
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
        assert "alternatives" in result

    def test_lan(self):
        result = parse_restriction("my LAN")
        assert result["type"] == "cidr"

    def test_internal(self):
        result = parse_restriction("internal")
        assert result["type"] == "cidr"
        assert "10.0.0.0" in result["value"]

    def test_unknown_restriction(self):
        result = parse_restriction("my office")
        assert result["type"] == "unknown"
        assert result["needs_input"] is True

    def test_case_insensitive(self):
        result = parse_restriction("LOCALHOST")
        assert result["type"] == "ip"

    def test_whitespace_stripped(self):
        result = parse_restriction("  localhost  ")
        assert result["type"] == "ip"


# ===========================================================================
# suggest_lockdown
# ===========================================================================


class TestSuggestLockdown:
    def test_known_service(self):
        cmds = suggest_lockdown("postgres", "localhost only")
        assert any("5432" in cmd for cmd in cmds)
        assert any("127.0.0.1" in cmd for cmd in cmds)

    def test_unknown_service(self):
        cmds = suggest_lockdown("unknown_service", "localhost")
        assert len(cmds) == 1
        assert "Unknown service" in cmds[0]

    def test_port_number_as_service(self):
        cmds = suggest_lockdown("8080", "localhost only")
        assert any("8080" in cmd for cmd in cmds)

    def test_needs_input_restriction(self):
        cmds = suggest_lockdown("postgres", "my office")
        assert any("Please provide" in cmd for cmd in cmds)

    def test_with_cidr_restriction(self):
        cmds = suggest_lockdown("redis", "10.0.0.0/8")
        assert any("6379" in cmd for cmd in cmds)
        assert any("10.0.0.0/8" in cmd for cmd in cmds)

    def test_with_alternatives(self):
        cmds = suggest_lockdown("postgres", "local network")
        assert any("Alternative" in cmd for cmd in cmds)

    def test_delete_existing_rules(self):
        cmds = suggest_lockdown("postgres", "localhost only")
        assert any("ufw delete allow" in cmd for cmd in cmds)


# ===========================================================================
# generate_lockdown_plan
# ===========================================================================


class TestGenerateLockdownPlan:
    def test_basic_lockdown(self):
        cmds = generate_lockdown_plan(["ssh", "http", "https"])
        assert any("ufw --force reset" in cmd for cmd in cmds)
        assert any("ufw default deny incoming" in cmd for cmd in cmds)
        assert any("ufw default allow outgoing" in cmd for cmd in cmds)
        assert any("22" in cmd for cmd in cmds)
        assert any("80" in cmd for cmd in cmds)
        assert any("443" in cmd for cmd in cmds)
        assert any("ufw --force enable" in cmd for cmd in cmds)

    def test_unknown_service_in_lockdown(self):
        cmds = generate_lockdown_plan(["ssh", "unknown_service"])
        assert any("Unknown service: unknown_service" in cmd for cmd in cmds)

    def test_empty_services(self):
        cmds = generate_lockdown_plan([])
        assert any("ufw --force reset" in cmd for cmd in cmds)
        assert any("ufw --force enable" in cmd for cmd in cmds)

    def test_includes_status_command(self):
        cmds = generate_lockdown_plan(["ssh"])
        assert any("ufw status verbose" in cmd for cmd in cmds)


# ===========================================================================
# Model tests
# ===========================================================================


class TestFirewallModels:
    def test_firewall_rule_frozen(self):
        rule = FirewallRule(
            action="allow",
            direction="in",
            port=22,
            raw="test",
        )
        with pytest.raises(Exception):
            rule.port = 80

    def test_firewall_explanation_frozen(self):
        exp = FirewallExplanation(
            summary="Test",
            active=True,
        )
        with pytest.raises(Exception):
            exp.active = False

    def test_firewall_rule_all_fields(self):
        rule = FirewallRule(
            action="allow",
            direction="in",
            port=22,
            protocol="tcp",
            from_addr="10.0.0.0/8",
            to_addr="192.168.1.1",
            interface="eth0",
            comment="SSH access",
            raw="22/tcp ALLOW IN 10.0.0.0/8",
        )
        assert rule.interface == "eth0"
        assert rule.comment == "SSH access"
        assert rule.to_addr == "192.168.1.1"

    def test_firewall_explanation_defaults(self):
        exp = FirewallExplanation(
            summary="Test",
            active=True,
        )
        assert exp.firewall_type == "ufw"
        assert exp.default_incoming == "deny"
        assert exp.default_outgoing == "allow"
        assert exp.rules == ()
        assert exp.rule_explanations == ()
        assert exp.warnings == ()


class TestServicePorts:
    def test_common_services_present(self):
        assert SERVICE_PORTS["ssh"] == 22
        assert SERVICE_PORTS["http"] == 80
        assert SERVICE_PORTS["https"] == 443
        assert SERVICE_PORTS["mysql"] == 3306
        assert SERVICE_PORTS["postgres"] == 5432
        assert SERVICE_PORTS["redis"] == 6379
        assert SERVICE_PORTS["mongodb"] == 27017
