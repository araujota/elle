"""Tests for policy models."""

from datetime import datetime, time

from elle.policy.models import (
    Condition,
    MatchType,
    PolicyDocument,
    PolicyEffect,
    PolicyEvaluationRequest,
    PolicyEvaluationResult,
    PolicyRule,
    TimeWindow,
)


class TestTimeWindow:
    """Tests for TimeWindow model."""

    def test_contains_same_day(self):
        """Test time window within same day."""
        window = TimeWindow(start=time(9, 0), end=time(17, 0))

        assert window.contains(time(9, 0))  # Start boundary
        assert window.contains(time(12, 0))  # Middle
        assert window.contains(time(17, 0))  # End boundary
        assert not window.contains(time(8, 59))  # Before
        assert not window.contains(time(17, 1))  # After

    def test_contains_spans_midnight(self):
        """Test time window spanning midnight."""
        window = TimeWindow(start=time(22, 0), end=time(6, 0))

        assert window.contains(time(22, 0))  # Start boundary
        assert window.contains(time(23, 30))  # Before midnight
        assert window.contains(time(0, 0))  # Midnight
        assert window.contains(time(3, 0))  # After midnight
        assert window.contains(time(6, 0))  # End boundary
        assert not window.contains(time(12, 0))  # Middle of day
        assert not window.contains(time(21, 59))  # Just before start


class TestCondition:
    """Tests for Condition model."""

    def test_command_condition(self):
        """Test condition with command pattern."""
        cond = Condition(command="rm -rf *", match_type=MatchType.GLOB)

        assert cond.command == "rm -rf *"
        assert cond.match_type == MatchType.GLOB
        assert cond.path is None
        assert not cond.negate

    def test_path_condition(self):
        """Test condition with path pattern."""
        cond = Condition(path="/etc/**/*.conf", match_type=MatchType.GLOB)

        assert cond.path == "/etc/**/*.conf"
        assert cond.command is None

    def test_negated_condition(self):
        """Test negated condition."""
        cond = Condition(
            command="safe-command",
            match_type=MatchType.EXACT,
            negate=True,
        )

        assert cond.negate is True

    def test_time_windowed_condition(self):
        """Test condition with time windows."""
        windows = (TimeWindow(start=time(2, 0), end=time(5, 0)),)
        cond = Condition(
            requires_privilege=True,
            allowed_hours=windows,
        )

        assert cond.allowed_hours is not None
        assert len(cond.allowed_hours) == 1


class TestPolicyRule:
    """Tests for PolicyRule model."""

    def test_basic_rule(self):
        """Test basic rule creation."""
        rule = PolicyRule(
            id="test-rule",
            name="Test Rule",
            conditions=(Condition(command="test", match_type=MatchType.EXACT),),
            effect=PolicyEffect.DENY,
            message="Test message",
        )

        assert rule.id == "test-rule"
        assert rule.effect == PolicyEffect.DENY
        assert rule.message == "Test message"
        assert rule.match_all is True  # Default
        assert rule.enabled is True  # Default

    def test_rule_with_justification(self):
        """Test rule requiring justification."""
        rule = PolicyRule(
            id="justify-rule",
            name="Justify Rule",
            conditions=(Condition(domain="network.*"),),
            effect=PolicyEffect.REQUIRE_JUSTIFICATION,
            justification_prompt="Why are you changing network settings?",
        )

        assert rule.effect == PolicyEffect.REQUIRE_JUSTIFICATION
        assert rule.justification_prompt is not None

    def test_rule_priority(self):
        """Test rule priority ordering."""
        high = PolicyRule(
            id="high-priority",
            name="High Priority",
            conditions=(Condition(command="*"),),
            effect=PolicyEffect.DENY,
            priority=100,
        )
        low = PolicyRule(
            id="low-priority",
            name="Low Priority",
            conditions=(Condition(command="*"),),
            effect=PolicyEffect.ALLOW,
            priority=0,
        )

        assert high.priority > low.priority


class TestPolicyDocument:
    """Tests for PolicyDocument model."""

    def test_empty_document(self):
        """Test empty policy document."""
        doc = PolicyDocument()

        assert doc.version == "1.0"
        assert doc.name == "Unnamed Policy"
        assert doc.extends == ()
        assert doc.default_effect == PolicyEffect.ALLOW
        assert doc.rules == ()

    def test_document_with_rules(self):
        """Test document with rules."""
        rule = PolicyRule(
            id="test",
            name="Test",
            conditions=(Condition(command="test"),),
            effect=PolicyEffect.DENY,
        )
        doc = PolicyDocument(
            name="Test Policy",
            rules=(rule,),
        )

        assert len(doc.rules) == 1
        assert doc.rules[0].id == "test"

    def test_document_with_extends(self):
        """Test document with extends."""
        doc = PolicyDocument(
            extends=("system:defaults", "custom.yaml"),
        )

        assert len(doc.extends) == 2
        assert "system:defaults" in doc.extends


class TestPolicyEvaluationRequest:
    """Tests for PolicyEvaluationRequest model."""

    def test_command_request(self):
        """Test command evaluation request."""
        request = PolicyEvaluationRequest(
            operation_type="command",
            command="rm -rf /tmp/test",
        )

        assert request.operation_type == "command"
        assert request.command == "rm -rf /tmp/test"
        assert request.requires_privilege is False

    def test_file_request(self):
        """Test file operation request."""
        request = PolicyEvaluationRequest(
            operation_type="file_write",
            path="/etc/ssh/sshd_config",
            requires_privilege=True,
        )

        assert request.operation_type == "file_write"
        assert request.path == "/etc/ssh/sshd_config"
        assert request.requires_privilege is True

    def test_request_with_timestamp(self):
        """Test request with custom timestamp."""
        ts = datetime(2024, 1, 1, 12, 0, 0)
        request = PolicyEvaluationRequest(
            operation_type="command",
            command="test",
            timestamp=ts,
        )

        assert request.timestamp == ts


class TestPolicyEvaluationResult:
    """Tests for PolicyEvaluationResult model."""

    def test_allowed_result(self):
        """Test allowed result."""
        result = PolicyEvaluationResult(
            allowed=True,
            effect=PolicyEffect.ALLOW,
        )

        assert result.allowed is True
        assert result.should_proceed is True
        assert result.is_blocked is False

    def test_denied_result(self):
        """Test denied result."""
        result = PolicyEvaluationResult(
            allowed=False,
            effect=PolicyEffect.DENY,
            message="Operation not allowed",
        )

        assert result.allowed is False
        assert result.should_proceed is False
        assert result.is_blocked is True
        assert result.message == "Operation not allowed"

    def test_confirmation_required(self):
        """Test confirmation required result."""
        result = PolicyEvaluationResult(
            allowed=True,
            effect=PolicyEffect.REQUIRE_CONFIRMATION,
            requires_confirmation=True,
            message="Please confirm",
        )

        assert result.should_proceed is True  # Can proceed with confirmation
        assert result.requires_confirmation is True
        assert result.is_blocked is False

    def test_justification_required(self):
        """Test justification required result."""
        result = PolicyEvaluationResult(
            allowed=True,
            effect=PolicyEffect.REQUIRE_JUSTIFICATION,
            requires_justification=True,
            justification_prompt="Why?",
        )

        assert result.should_proceed is True
        assert result.requires_justification is True
        assert result.justification_prompt == "Why?"

    def test_matched_rules(self):
        """Test result with matched rules."""
        result = PolicyEvaluationResult(
            allowed=False,
            effect=PolicyEffect.DENY,
            matched_rules=("rule-1", "rule-2"),
        )

        assert len(result.matched_rules) == 2
        assert "rule-1" in result.matched_rules
