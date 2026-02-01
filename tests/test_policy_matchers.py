"""Tests for policy matchers."""

from datetime import time

from elle.policy.matchers import (
    match_condition,
    match_domain,
    match_exact,
    match_glob,
    match_pattern,
    match_prefix,
    match_regex,
    match_time_window,
)
from elle.policy.models import (
    Condition,
    MatchType,
    PolicyEvaluationRequest,
    RiskLevel,
    TimeWindow,
)


class TestExactMatching:
    """Tests for exact matching."""

    def test_exact_match(self):
        """Test exact string matching."""
        assert match_exact("hello", "hello") is True
        assert match_exact("hello", "Hello") is False  # Case sensitive
        assert match_exact("hello", "hello world") is False

    def test_pattern_match_exact(self):
        """Test pattern matching with exact type."""
        assert match_pattern("test", "test", MatchType.EXACT) is True
        assert match_pattern("test", "TEST", MatchType.EXACT) is False


class TestPrefixMatching:
    """Tests for prefix matching."""

    def test_prefix_match(self):
        """Test prefix matching."""
        assert match_prefix("sudo", "sudo apt update") is True
        assert match_prefix("sudo", "apt update") is False
        assert match_prefix("/etc", "/etc/passwd") is True

    def test_pattern_match_prefix(self):
        """Test pattern matching with prefix type."""
        assert match_pattern("rm -rf", "rm -rf /tmp", MatchType.PREFIX) is True
        assert match_pattern("rm -rf", "ls -la", MatchType.PREFIX) is False


class TestGlobMatching:
    """Tests for glob pattern matching."""

    def test_glob_basic(self):
        """Test basic glob patterns."""
        assert match_glob("*.txt", "file.txt") is True
        assert match_glob("*.txt", "file.py") is False
        assert match_glob("file.*", "file.txt") is True

    def test_glob_question_mark(self):
        """Test glob ? wildcard."""
        assert match_glob("file?.txt", "file1.txt") is True
        assert match_glob("file?.txt", "file12.txt") is False

    def test_glob_recursive(self):
        """Test ** recursive glob."""
        assert match_glob("/etc/**/*.conf", "/etc/ssh/sshd_config.conf") is True
        assert match_glob("/etc/**/*.conf", "/etc/config.conf") is True
        assert match_glob("/etc/**", "/etc/ssh/nested/file") is True

    def test_glob_character_class(self):
        """Test character class [abc]."""
        assert match_glob("file[123].txt", "file1.txt") is True
        assert match_glob("file[123].txt", "file4.txt") is False

    def test_pattern_match_glob(self):
        """Test pattern matching with glob type."""
        assert match_pattern("/etc/*.conf", "/etc/hosts.conf", MatchType.GLOB) is True
        assert match_pattern("/etc/*.conf", "/var/hosts.conf", MatchType.GLOB) is False


class TestRegexMatching:
    """Tests for regex pattern matching."""

    def test_regex_basic(self):
        """Test basic regex patterns."""
        assert match_regex(r"rm\s+-rf", "rm -rf /tmp") is True
        assert match_regex(r"rm\s+-rf", "rm /tmp") is False

    def test_regex_case_insensitive(self):
        """Test regex is case insensitive."""
        assert match_regex(r"sudo", "SUDO apt") is True
        assert match_regex(r"SUDO", "sudo apt") is True

    def test_regex_anchors(self):
        """Test regex with anchors."""
        assert match_regex(r"^rm", "rm -rf") is True
        assert match_regex(r"^rm", "sudo rm") is False

    def test_pattern_match_regex(self):
        """Test pattern matching with regex type."""
        assert match_pattern(r"dd\s+.*of=/dev", "dd if=/dev/zero of=/dev/sda", MatchType.REGEX) is True


class TestDomainMatching:
    """Tests for domain pattern matching."""

    def test_exact_domain(self):
        """Test exact domain matching."""
        assert match_domain("network.interface", "network.interface") is True
        assert match_domain("network.interface", "network.route") is False

    def test_wildcard_domain(self):
        """Test wildcard domain matching."""
        assert match_domain("network.*", "network.interface") is True
        assert match_domain("network.*", "network.route") is True
        assert match_domain("network.*", "service.systemd") is False

    def test_wildcard_middle(self):
        """Test wildcard in middle of domain."""
        assert match_domain("*.config", "system.config") is True
        assert match_domain("*.config", "user.config") is True
        assert match_domain("*.config", "system.service") is False

    def test_recursive_domain(self):
        """Test ** recursive domain matching."""
        assert match_domain("service.**", "service.systemd.unit") is True
        assert match_domain("service.**", "service.nginx") is True


class TestTimeWindowMatching:
    """Tests for time window matching."""

    def test_within_window(self):
        """Test time within window."""
        windows = (TimeWindow(start=time(9, 0), end=time(17, 0)),)

        assert match_time_window(windows, time(12, 0)) is True
        assert match_time_window(windows, time(9, 0)) is True
        assert match_time_window(windows, time(17, 0)) is True

    def test_outside_window(self):
        """Test time outside window."""
        windows = (TimeWindow(start=time(9, 0), end=time(17, 0)),)

        assert match_time_window(windows, time(8, 0)) is False
        assert match_time_window(windows, time(18, 0)) is False

    def test_multiple_windows(self):
        """Test multiple time windows (OR)."""
        windows = (
            TimeWindow(start=time(9, 0), end=time(12, 0)),
            TimeWindow(start=time(14, 0), end=time(17, 0)),
        )

        assert match_time_window(windows, time(10, 0)) is True
        assert match_time_window(windows, time(15, 0)) is True
        assert match_time_window(windows, time(13, 0)) is False  # Lunch break

    def test_empty_windows(self):
        """Test empty windows always matches."""
        assert match_time_window((), time(12, 0)) is True


class TestConditionMatching:
    """Tests for full condition matching."""

    def test_command_condition(self):
        """Test command condition matching."""
        condition = Condition(command="sudo *", match_type=MatchType.GLOB)
        request = PolicyEvaluationRequest(
            operation_type="command",
            command="sudo apt update",
        )

        assert match_condition(condition, request) is True

        request2 = PolicyEvaluationRequest(
            operation_type="command",
            command="apt update",
        )
        assert match_condition(condition, request2) is False

    def test_path_condition(self):
        """Test path condition matching."""
        condition = Condition(path="/etc/**", match_type=MatchType.GLOB)
        request = PolicyEvaluationRequest(
            operation_type="file_write",
            path="/etc/ssh/sshd_config",
        )

        assert match_condition(condition, request) is True

    def test_domain_condition(self):
        """Test domain condition matching."""
        condition = Condition(domain="network.*")
        request = PolicyEvaluationRequest(
            operation_type="config_edit",
            domain="network.interface",
        )

        assert match_condition(condition, request) is True

    def test_risk_level_condition(self):
        """Test risk level condition matching."""
        condition = Condition(risk_level=RiskLevel.HIGH)
        request_high = PolicyEvaluationRequest(
            operation_type="command",
            risk_level=RiskLevel.HIGH,
        )
        request_low = PolicyEvaluationRequest(
            operation_type="command",
            risk_level=RiskLevel.LOW,
        )

        assert match_condition(condition, request_high) is True
        assert match_condition(condition, request_low) is False

    def test_privilege_condition(self):
        """Test privilege requirement condition."""
        condition = Condition(requires_privilege=True)
        request_priv = PolicyEvaluationRequest(
            operation_type="command",
            requires_privilege=True,
        )
        request_no_priv = PolicyEvaluationRequest(
            operation_type="command",
            requires_privilege=False,
        )

        assert match_condition(condition, request_priv) is True
        assert match_condition(condition, request_no_priv) is False

    def test_negated_condition(self):
        """Test negated condition matching."""
        condition = Condition(
            command="safe-command",
            match_type=MatchType.EXACT,
            negate=True,
        )
        request_match = PolicyEvaluationRequest(
            operation_type="command",
            command="safe-command",
        )
        request_no_match = PolicyEvaluationRequest(
            operation_type="command",
            command="other-command",
        )

        # Negated: true becomes false, false becomes true
        assert match_condition(condition, request_match) is False
        assert match_condition(condition, request_no_match) is True

    def test_multiple_fields_condition(self):
        """Test condition with multiple fields (AND)."""
        condition = Condition(
            command="rm *",
            match_type=MatchType.GLOB,
            requires_privilege=False,
        )
        request_both = PolicyEvaluationRequest(
            operation_type="command",
            command="rm test.txt",
            requires_privilege=False,
        )
        request_cmd_only = PolicyEvaluationRequest(
            operation_type="command",
            command="rm test.txt",
            requires_privilege=True,  # Wrong privilege
        )

        assert match_condition(condition, request_both) is True
        assert match_condition(condition, request_cmd_only) is False  # Privilege doesn't match

    def test_missing_request_field(self):
        """Test condition when request lacks required field."""
        condition = Condition(command="test", match_type=MatchType.EXACT)
        request = PolicyEvaluationRequest(
            operation_type="command",
            command=None,  # No command
        )

        assert match_condition(condition, request) is False


# =============================================================================
# Additional coverage tests (lines 44, 105, 108-112, 133-134, 149, 186-187,
# 229, 262, 312, 314, 319, 321, 325-328, 333, 346-348)
# =============================================================================


class TestExactCaseInsensitiveMatching:
    """Tests for match_exact_ci (line 44)."""

    def test_case_insensitive_match(self):
        """Test case-insensitive exact matching returns True."""
        from elle.policy.matchers import match_exact_ci

        assert match_exact_ci("Hello", "hello") is True
        assert match_exact_ci("HELLO", "hello") is True
        assert match_exact_ci("hello", "HELLO") is True

    def test_case_insensitive_mismatch(self):
        """Test case-insensitive exact matching returns False for different strings."""
        from elle.policy.matchers import match_exact_ci

        assert match_exact_ci("hello", "world") is False
        assert match_exact_ci("abc", "abcd") is False


class TestGlobToRegexBranches:
    """Tests for _glob_to_regex branches: ? wildcard (line 105) and [ ] char class (lines 108-112)."""

    def test_question_mark_in_doublestar_pattern(self):
        """Test ? wildcard inside a ** glob pattern (line 105)."""
        # ? in a ** pattern goes through _glob_to_regex which adds [^/]
        assert match_glob("/**/file?.txt", "/foo/file1.txt") is True
        assert match_glob("/**/file?.txt", "/foo/file12.txt") is False

    def test_character_class_in_doublestar_pattern(self):
        """Test [abc] character class inside a ** glob pattern (lines 108-112)."""
        assert match_glob("/**/file[123].txt", "/foo/file1.txt") is True
        assert match_glob("/**/file[123].txt", "/foo/file4.txt") is False
        assert match_glob("/**/[abc].txt", "/dir/a.txt") is True
        assert match_glob("/**/[abc].txt", "/dir/d.txt") is False


class TestRegexCompileFailure:
    """Tests for _compile_regex returning None on invalid regex (lines 133-134, 149)."""

    def test_invalid_regex_returns_false(self):
        """Test match_regex with invalid pattern returns False (lines 133-134, 149)."""
        assert match_regex("[invalid", "test value") is False
        assert match_regex("(unclosed", "test") is False


class TestMatchPatternDefaultCase:
    """Tests for the default/fallback case in match_pattern (lines 186-187)."""

    def test_unknown_match_type_returns_false(self):
        """Test match_pattern with an unrecognized match type hits the default case."""
        from unittest.mock import MagicMock

        # Create a mock that looks like a MatchType but is not one of the known values
        fake_type = MagicMock()
        fake_type.value = "unknown"
        # match_pattern uses structural pattern matching; a MagicMock won't match
        # any of the known MatchType cases, so it falls to the default.
        assert match_pattern("test", "test", fake_type) is False


class TestDomainLengthMismatch:
    """Tests for domain matching when part counts differ (line 229)."""

    def test_different_depth_returns_false(self):
        """Test domain with different number of parts returns False (line 229)."""
        assert match_domain("network", "network.interface") is False
        assert match_domain("a.b.c", "a.b") is False
        assert match_domain("x", "x.y.z") is False


class TestTimeWindowDefaultTime:
    """Tests for match_time_window with check_time=None (line 262)."""

    def test_none_check_time_uses_current(self):
        """Test match_time_window with None uses datetime.now() (line 262)."""
        # A window covering the entire day should always match
        full_day = (TimeWindow(start=time(0, 0), end=time(23, 59, 59)),)
        assert match_time_window(full_day, None) is True

    def test_no_check_time_argument(self):
        """Test match_time_window default argument (check_time defaults to None)."""
        full_day = (TimeWindow(start=time(0, 0), end=time(23, 59, 59)),)
        assert match_time_window(full_day) is True


class TestConditionPathBranches:
    """Tests for path condition branches (lines 312, 314)."""

    def test_path_condition_request_path_none(self):
        """Test path condition when request.path is None (line 312)."""
        condition = Condition(path="/etc/**", match_type=MatchType.GLOB)
        request = PolicyEvaluationRequest(
            operation_type="file_write",
            path=None,
        )
        assert match_condition(condition, request) is False

    def test_path_condition_no_match(self):
        """Test path condition when pattern does not match (line 314)."""
        condition = Condition(path="/etc/**", match_type=MatchType.GLOB)
        request = PolicyEvaluationRequest(
            operation_type="file_write",
            path="/var/log/syslog",
        )
        assert match_condition(condition, request) is False


class TestConditionDomainBranches:
    """Tests for domain condition branches (lines 319, 321)."""

    def test_domain_condition_request_domain_none(self):
        """Test domain condition when request.domain is None (line 319)."""
        condition = Condition(domain="network.*")
        request = PolicyEvaluationRequest(
            operation_type="config_edit",
            domain=None,
        )
        assert match_condition(condition, request) is False

    def test_domain_condition_no_match(self):
        """Test domain condition when domain does not match (line 321)."""
        condition = Condition(domain="network.*")
        request = PolicyEvaluationRequest(
            operation_type="config_edit",
            domain="service.systemd",
        )
        assert match_condition(condition, request) is False


class TestConditionIntentBranches:
    """Tests for intent condition branches (lines 325-328)."""

    def test_intent_condition_match(self):
        """Test intent condition matching (line 327-328 true path)."""
        condition = Condition(intent="shell_*", match_type=MatchType.GLOB)
        request = PolicyEvaluationRequest(
            operation_type="command",
            intent="shell_passthrough",
        )
        assert match_condition(condition, request) is True

    def test_intent_condition_no_match(self):
        """Test intent condition when intent does not match (line 327-328 false path)."""
        condition = Condition(intent="shell_*", match_type=MatchType.GLOB)
        request = PolicyEvaluationRequest(
            operation_type="command",
            intent="system_question",
        )
        assert match_condition(condition, request) is False

    def test_intent_condition_request_intent_none(self):
        """Test intent condition when request.intent is None (lines 325-326)."""
        condition = Condition(intent="shell_passthrough", match_type=MatchType.EXACT)
        request = PolicyEvaluationRequest(
            operation_type="command",
            intent=None,
        )
        assert match_condition(condition, request) is False


class TestConditionRiskLevelNone:
    """Tests for risk_level condition when request.risk_level is None (line 333)."""

    def test_risk_condition_request_risk_none(self):
        """Test risk condition when request has no risk level (line 333)."""
        condition = Condition(risk_level=RiskLevel.HIGH)
        request = PolicyEvaluationRequest(
            operation_type="command",
            risk_level=None,
        )
        assert match_condition(condition, request) is False


class TestConditionAllowedHours:
    """Tests for allowed_hours condition on Condition (lines 346-348)."""

    def test_allowed_hours_within_window(self):
        """Test allowed_hours condition when time is in window (lines 346-348 pass)."""
        from datetime import datetime as dt

        window = TimeWindow(start=time(9, 0), end=time(17, 0))
        condition = Condition(allowed_hours=(window,))
        # Create request with timestamp at noon
        request = PolicyEvaluationRequest(
            operation_type="command",
            timestamp=dt(2024, 6, 15, 12, 0, 0),
        )
        assert match_condition(condition, request) is True

    def test_allowed_hours_outside_window(self):
        """Test allowed_hours condition when time is outside window (lines 346-348 fail)."""
        from datetime import datetime as dt

        window = TimeWindow(start=time(9, 0), end=time(17, 0))
        condition = Condition(allowed_hours=(window,))
        # Create request with timestamp at 2 AM
        request = PolicyEvaluationRequest(
            operation_type="command",
            timestamp=dt(2024, 6, 15, 2, 0, 0),
        )
        assert match_condition(condition, request) is False

    def test_allowed_hours_no_timestamp(self):
        """Test allowed_hours condition using request.timestamp for time extraction."""
        window = TimeWindow(start=time(0, 0), end=time(23, 59, 59))
        condition = Condition(allowed_hours=(window,))
        # Default timestamp is datetime.now(), full-day window should match
        request = PolicyEvaluationRequest(
            operation_type="command",
        )
        assert match_condition(condition, request) is True
