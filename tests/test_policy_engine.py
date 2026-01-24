"""Tests for policy engine."""

import pytest
import tempfile
from pathlib import Path

from elle.policy.defaults import get_default_policy, get_default_rules
from elle.policy.engine import PolicyEngine, evaluate_command
from elle.policy.models import (
    Condition,
    MatchType,
    PolicyDocument,
    PolicyEffect,
    PolicyEvaluationRequest,
    PolicyRule,
)
from elle.policy.parser import parse_string


class TestPolicyEngine:
    """Tests for PolicyEngine class."""

    def test_engine_with_empty_policy(self):
        """Test engine with empty policy uses default effect."""
        policy = PolicyDocument(default_effect=PolicyEffect.ALLOW)
        engine = PolicyEngine(policy=policy)

        result = engine.evaluate_command("ls -la", audit=False)

        assert result.allowed is True
        assert result.effect == PolicyEffect.ALLOW

    def test_engine_with_deny_rule(self):
        """Test engine with deny rule."""
        rule = PolicyRule(
            id="deny-test",
            name="Deny Test",
            conditions=(
                Condition(command="sudo *", match_type=MatchType.GLOB),
            ),
            effect=PolicyEffect.DENY,
            message="Sudo not allowed",
        )
        policy = PolicyDocument(rules=(rule,))
        engine = PolicyEngine(policy=policy)

        result = engine.evaluate_command("sudo apt update", audit=False)

        assert result.allowed is False
        assert result.effect == PolicyEffect.DENY
        assert result.message == "Sudo not allowed"
        assert "deny-test" in result.matched_rules

    def test_engine_with_allow_rule(self):
        """Test engine with explicit allow rule."""
        rule = PolicyRule(
            id="allow-ls",
            name="Allow ls",
            conditions=(
                Condition(command="ls *", match_type=MatchType.GLOB),
            ),
            effect=PolicyEffect.ALLOW,
        )
        policy = PolicyDocument(
            rules=(rule,),
            default_effect=PolicyEffect.DENY,
        )
        engine = PolicyEngine(policy=policy)

        # ls should be allowed
        result = engine.evaluate_command("ls -la", audit=False)
        assert result.allowed is True

        # Other commands should be denied (default)
        result2 = engine.evaluate_command("cat /etc/passwd", audit=False)
        assert result2.allowed is False

    def test_engine_rule_priority(self):
        """Test that higher priority rules are evaluated first."""
        allow_rule = PolicyRule(
            id="allow-all",
            name="Allow All",
            conditions=(Condition(command="*", match_type=MatchType.GLOB),),
            effect=PolicyEffect.ALLOW,
            priority=0,  # Low priority
        )
        deny_rule = PolicyRule(
            id="deny-sudo",
            name="Deny Sudo",
            conditions=(Condition(command="sudo *", match_type=MatchType.GLOB),),
            effect=PolicyEffect.DENY,
            priority=100,  # High priority
        )
        policy = PolicyDocument(rules=(allow_rule, deny_rule))
        engine = PolicyEngine(policy=policy)

        # sudo should be denied (higher priority)
        result = engine.evaluate_command("sudo apt update", audit=False)
        assert result.allowed is False

    def test_engine_confirmation_required(self):
        """Test engine with confirmation requirement."""
        rule = PolicyRule(
            id="confirm-reboot",
            name="Confirm Reboot",
            conditions=(
                Condition(command="reboot", match_type=MatchType.EXACT),
            ),
            effect=PolicyEffect.REQUIRE_CONFIRMATION,
            message="This will reboot the system",
        )
        policy = PolicyDocument(rules=(rule,))
        engine = PolicyEngine(policy=policy)

        result = engine.evaluate_command("reboot", audit=False)

        assert result.requires_confirmation is True
        assert result.should_proceed is True  # Can proceed with confirmation
        assert result.message == "This will reboot the system"

    def test_engine_justification_required(self):
        """Test engine with justification requirement."""
        rule = PolicyRule(
            id="justify-network",
            name="Justify Network Changes",
            conditions=(
                Condition(domain="network.*"),
            ),
            effect=PolicyEffect.REQUIRE_JUSTIFICATION,
            justification_prompt="Why are you changing network settings?",
        )
        policy = PolicyDocument(rules=(rule,))
        engine = PolicyEngine(policy=policy)

        request = PolicyEvaluationRequest(
            operation_type="config_edit",
            domain="network.interface",
        )
        result = engine.evaluate(request, audit=False)

        assert result.requires_justification is True
        assert result.justification_prompt == "Why are you changing network settings?"

    def test_engine_path_evaluation(self):
        """Test engine with path-based evaluation."""
        rule = PolicyRule(
            id="deny-etc",
            name="Deny /etc modifications",
            conditions=(
                Condition(path="/etc/**", match_type=MatchType.GLOB),
            ),
            effect=PolicyEffect.DENY,
            message="Cannot modify /etc files",
        )
        policy = PolicyDocument(rules=(rule,))
        engine = PolicyEngine(policy=policy)

        result = engine.evaluate_path("/etc/ssh/sshd_config", audit=False)

        assert result.allowed is False
        assert result.message == "Cannot modify /etc files"

    def test_engine_disabled_rule(self):
        """Test that disabled rules are skipped."""
        rule = PolicyRule(
            id="deny-all",
            name="Deny All",
            conditions=(Condition(command="*", match_type=MatchType.GLOB),),
            effect=PolicyEffect.DENY,
            enabled=False,  # Disabled
        )
        policy = PolicyDocument(rules=(rule,))
        engine = PolicyEngine(policy=policy)

        result = engine.evaluate_command("ls", audit=False)

        assert result.allowed is True  # Rule was skipped

    def test_engine_multiple_conditions_and(self):
        """Test rule with multiple conditions (AND)."""
        rule = PolicyRule(
            id="deny-priv-network",
            name="Deny Privileged Network",
            conditions=(
                Condition(domain="network.*"),
                Condition(requires_privilege=True),
            ),
            match_all=True,  # AND
            effect=PolicyEffect.DENY,
        )
        policy = PolicyDocument(rules=(rule,))
        engine = PolicyEngine(policy=policy)

        # Both conditions match -> denied
        request1 = PolicyEvaluationRequest(
            operation_type="config_edit",
            domain="network.interface",
            requires_privilege=True,
        )
        assert engine.evaluate(request1, audit=False).allowed is False

        # Only one condition matches -> allowed
        request2 = PolicyEvaluationRequest(
            operation_type="config_edit",
            domain="network.interface",
            requires_privilege=False,
        )
        assert engine.evaluate(request2, audit=False).allowed is True

    def test_engine_multiple_conditions_or(self):
        """Test rule with multiple conditions (OR)."""
        rule = PolicyRule(
            id="deny-dangerous",
            name="Deny Dangerous",
            conditions=(
                Condition(command="rm *", match_type=MatchType.GLOB),
                Condition(command="dd *", match_type=MatchType.GLOB),
            ),
            match_all=False,  # OR
            effect=PolicyEffect.DENY,
        )
        policy = PolicyDocument(rules=(rule,))
        engine = PolicyEngine(policy=policy)

        # First condition matches
        assert engine.evaluate_command("rm -rf /", audit=False).allowed is False

        # Second condition matches
        assert engine.evaluate_command("dd if=/dev/zero", audit=False).allowed is False

        # Neither matches
        assert engine.evaluate_command("ls -la", audit=False).allowed is True


class TestDefaultPolicy:
    """Tests for default policy rules."""

    def test_default_policy_exists(self):
        """Test that default policy is available."""
        policy = get_default_policy()

        assert policy is not None
        assert len(policy.rules) > 0

    def test_default_rules_count(self):
        """Test that default rules are defined."""
        rules = get_default_rules()

        # Should have many rules from subprocess_runner patterns
        assert len(rules) > 20

    def test_sudo_blocked(self):
        """Test that sudo commands are blocked by default."""
        policy = get_default_policy()
        engine = PolicyEngine(policy=policy)

        result = engine.evaluate_command("sudo apt update", audit=False)

        assert result.allowed is False
        assert "sudo" in result.message.lower() if result.message else False

    def test_destructive_rm_blocked(self):
        """Test that destructive rm is blocked by default."""
        policy = get_default_policy()
        engine = PolicyEngine(policy=policy)

        result = engine.evaluate_command("rm -rf /", audit=False)

        assert result.allowed is False

    def test_safe_commands_allowed(self):
        """Test that safe commands are allowed by default."""
        policy = get_default_policy()
        engine = PolicyEngine(policy=policy)

        # Safe commands
        assert engine.evaluate_command("ls -la", audit=False).allowed is True
        assert engine.evaluate_command("cat /etc/hostname", audit=False).allowed is True
        assert engine.evaluate_command("echo hello", audit=False).allowed is True


class TestPolicyParsing:
    """Tests for policy parsing integration."""

    def test_parse_and_evaluate(self):
        """Test parsing a policy and evaluating against it."""
        yaml_content = """
version: "1.0"
name: "Test Policy"
default_effect: deny

rules:
  - id: "allow-ls"
    name: "Allow ls command"
    effect: allow
    conditions:
      - command: "ls*"
        match_type: glob
"""
        policy = parse_string(yaml_content)
        engine = PolicyEngine(policy=policy)

        # ls should be allowed
        assert engine.evaluate_command("ls -la", audit=False).allowed is True

        # Other commands denied
        assert engine.evaluate_command("cat file", audit=False).allowed is False

    def test_parse_complex_policy(self):
        """Test parsing a complex policy with multiple rules."""
        yaml_content = """
version: "1.0"
name: "Complex Policy"
default_effect: allow

rules:
  - id: "deny-sudo"
    name: "Deny sudo"
    effect: deny
    priority: 100
    conditions:
      - command: "sudo "
        match_type: prefix
    message: "Sudo not allowed"

  - id: "require-confirm-rm"
    name: "Require confirmation for rm"
    effect: require_confirmation
    priority: 50
    conditions:
      - command: "rm *"
        match_type: glob
    message: "Are you sure you want to delete?"
"""
        policy = parse_string(yaml_content)
        engine = PolicyEngine(policy=policy)

        # sudo denied
        result = engine.evaluate_command("sudo apt update", audit=False)
        assert result.effect == PolicyEffect.DENY

        # rm requires confirmation
        result = engine.evaluate_command("rm test.txt", audit=False)
        assert result.effect == PolicyEffect.REQUIRE_CONFIRMATION
        assert result.requires_confirmation is True


class TestModuleLevelFunctions:
    """Tests for module-level convenience functions."""

    def test_evaluate_command_function(self):
        """Test the evaluate_command convenience function."""
        # This uses the global engine with default policy
        result = evaluate_command("ls -la", audit=False)

        assert result.allowed is True

    def test_evaluate_command_denied(self):
        """Test evaluate_command with denied command."""
        result = evaluate_command("sudo rm -rf /", audit=False)

        assert result.allowed is False
