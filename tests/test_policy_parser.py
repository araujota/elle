from __future__ import annotations

"""Tests for policy parser module (elle.policy.parser).

Covers PolicyParser class methods, module-level convenience functions,
and all error/edge-case branches.
"""

import sys
from datetime import time
from pathlib import Path
from unittest.mock import patch

import pytest

from elle.policy.exceptions import PolicyParseError
from elle.policy.models import (
    MatchType,
    PolicyDocument,
    PolicyEffect,
    RiskLevel,
)
from elle.policy.parser import PolicyParser, get_parser, parse_file, parse_string

# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def parser() -> PolicyParser:
    """Return a fresh PolicyParser instance."""
    return PolicyParser()


MINIMAL_YAML = """\
version: "1.0"
name: "Minimal Policy"
default_effect: allow
rules: []
"""

FULL_YAML = """\
version: "2.0"
name: "Full Policy"
extends:
  - "base.yaml"
  - "extra.yaml"
default_effect: deny
rules:
  - id: "deny-sudo"
    name: "Deny sudo"
    effect: deny
    priority: 100
    enabled: true
    match_all: true
    message: "Sudo is blocked"
    conditions:
      - command: "sudo *"
        match_type: glob
  - id: "confirm-rm"
    name: "Confirm rm"
    effect: require_confirmation
    priority: 50
    enabled: false
    match_all: false
    message: "Are you sure?"
    justification_prompt: "Why delete?"
    conditions:
      - command: "rm *"
        match_type: glob
      - path: "/etc/**"
        match_type: glob
        negate: true
"""


# =========================================================================
# TestPolicyParser -- parse_string
# =========================================================================


class TestParseString:
    """Tests for PolicyParser.parse_string."""

    def test_minimal_yaml(self, parser: PolicyParser):
        """Minimal valid policy parses correctly."""
        doc = parser.parse_string(MINIMAL_YAML)

        assert isinstance(doc, PolicyDocument)
        assert doc.version == "1.0"
        assert doc.name == "Minimal Policy"
        assert doc.default_effect == PolicyEffect.ALLOW
        assert doc.rules == ()

    def test_full_yaml(self, parser: PolicyParser):
        """Full policy with multiple rules and extends parses correctly."""
        doc = parser.parse_string(FULL_YAML)

        assert doc.version == "2.0"
        assert doc.name == "Full Policy"
        assert doc.extends == ("base.yaml", "extra.yaml")
        assert doc.default_effect == PolicyEffect.DENY
        assert len(doc.rules) == 2

        rule0 = doc.rules[0]
        assert rule0.id == "deny-sudo"
        assert rule0.name == "Deny sudo"
        assert rule0.effect == PolicyEffect.DENY
        assert rule0.priority == 100
        assert rule0.enabled is True
        assert rule0.match_all is True
        assert rule0.message == "Sudo is blocked"

        rule1 = doc.rules[1]
        assert rule1.id == "confirm-rm"
        assert rule1.enabled is False
        assert rule1.match_all is False
        assert rule1.justification_prompt == "Why delete?"
        assert len(rule1.conditions) == 2
        assert rule1.conditions[1].negate is True

    def test_defaults_when_fields_absent(self, parser: PolicyParser):
        """Missing optional fields receive sensible defaults."""
        yaml = """\
rules: []
"""
        doc = parser.parse_string(yaml)

        assert doc.version == "1.0"
        assert doc.name == "Unnamed Policy"
        assert doc.extends == ()
        assert doc.default_effect == PolicyEffect.ALLOW

    def test_extends_as_string(self, parser: PolicyParser):
        """A string value for extends is wrapped in a tuple."""
        yaml = """\
extends: "base.yaml"
rules: []
"""
        doc = parser.parse_string(yaml)

        assert doc.extends == ("base.yaml",)

    def test_extends_as_non_list_non_string(self, parser: PolicyParser):
        """Non-list, non-string extends falls through to empty tuple."""
        yaml = """\
extends: 42
rules: []
"""
        doc = parser.parse_string(yaml)

        assert doc.extends == ()

    def test_invalid_yaml_syntax(self, parser: PolicyParser):
        """Malformed YAML raises PolicyParseError."""
        # Tabs are not allowed in YAML for indentation -- triggers a parse error
        bad_yaml = "key:\n\t- bad indent\n"
        with pytest.raises(PolicyParseError, match="Invalid YAML"):
            parser.parse_string(bad_yaml)

    def test_yaml_not_a_mapping(self, parser: PolicyParser):
        """YAML that evaluates to a non-dict raises PolicyParseError."""
        with pytest.raises(PolicyParseError, match="must be a YAML mapping"):
            parser.parse_string("- just\n- a\n- list\n")

    def test_invalid_default_effect(self, parser: PolicyParser):
        """Invalid default_effect value raises PolicyParseError."""
        yaml = """\
default_effect: "explode"
rules: []
"""
        with pytest.raises(PolicyParseError, match="Invalid default_effect"):
            parser.parse_string(yaml)

    def test_rules_not_a_list(self, parser: PolicyParser):
        """rules as a mapping raises PolicyParseError."""
        yaml = """\
rules:
  id: oops
"""
        with pytest.raises(PolicyParseError, match="rules must be a list"):
            parser.parse_string(yaml)

    def test_ruamel_import_error(self, parser: PolicyParser):
        """Missing ruamel.yaml raises PolicyParseError."""
        with patch.dict(sys.modules, {"ruamel.yaml": None, "ruamel": None}):
            # Force the lazy import inside parse_string to fail
            with patch(
                "builtins.__import__",
                side_effect=ImportError("no ruamel"),
            ):
                with pytest.raises(PolicyParseError, match="ruamel.yaml is not installed"):
                    parser.parse_string("version: 1\nrules: []")

    def test_yaml_error_with_problem_mark(self, parser: PolicyParser):
        """YAML error that includes a problem_mark reports the line number."""
        # Indentation errors typically produce a problem_mark
        bad_yaml = "key:\n  sub: 1\n sub2: 2\n"
        with pytest.raises(PolicyParseError) as exc_info:
            parser.parse_string(bad_yaml)
        assert "Invalid YAML" in str(exc_info.value)

    def test_source_propagates_in_errors(self, parser: PolicyParser):
        """The source parameter appears in parse errors."""
        with pytest.raises(PolicyParseError, match="my_source.yaml"):
            parser.parse_string("- not a mapping", source="my_source.yaml")


# =========================================================================
# TestPolicyParser -- parse_file
# =========================================================================


class TestParseFile:
    """Tests for PolicyParser.parse_file."""

    def test_parse_file_success(self, parser: PolicyParser, tmp_path: Path):
        """Reads a YAML file from disk and returns a PolicyDocument."""
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(MINIMAL_YAML, encoding="utf-8")

        doc = parser.parse_file(policy_file)

        assert doc.name == "Minimal Policy"

    def test_parse_file_accepts_string_path(self, parser: PolicyParser, tmp_path: Path):
        """parse_file also accepts a plain string path."""
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(MINIMAL_YAML, encoding="utf-8")

        doc = parser.parse_file(str(policy_file))

        assert isinstance(doc, PolicyDocument)

    def test_parse_file_not_found(self, parser: PolicyParser):
        """Missing file raises PolicyParseError."""
        with pytest.raises(PolicyParseError, match="File not found"):
            parser.parse_file("/nonexistent/path/policy.yaml")

    def test_parse_file_os_error(self, parser: PolicyParser, tmp_path: Path):
        """Unreadable file raises PolicyParseError wrapping OSError."""
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text("dummy", encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            with pytest.raises(PolicyParseError, match="Cannot read file"):
                parser.parse_file(policy_file)


# =========================================================================
# TestParseRule
# =========================================================================


class TestParseRule:
    """Tests for PolicyParser._parse_rule."""

    def test_rule_not_a_dict(self, parser: PolicyParser):
        """Non-dict rule data raises PolicyValidationError."""
        yaml = """\
rules:
  - "not a mapping"
"""
        with pytest.raises(PolicyParseError, match="Rule must be a mapping"):
            parser.parse_string(yaml)

    def test_rule_missing_id(self, parser: PolicyParser):
        """Rule without id raises PolicyValidationError."""
        yaml = """\
rules:
  - effect: allow
    conditions:
      - command: "ls"
"""
        with pytest.raises(PolicyParseError, match="Missing required field: id"):
            parser.parse_string(yaml)

    def test_rule_missing_effect(self, parser: PolicyParser):
        """Rule without effect raises PolicyValidationError."""
        yaml = """\
rules:
  - id: "no-effect"
    conditions:
      - command: "ls"
"""
        with pytest.raises(PolicyParseError, match="Missing required field: effect"):
            parser.parse_string(yaml)

    def test_rule_invalid_effect(self, parser: PolicyParser):
        """Rule with unknown effect raises PolicyValidationError."""
        yaml = """\
rules:
  - id: "bad-effect"
    effect: "nuke"
    conditions:
      - command: "ls"
"""
        with pytest.raises(PolicyParseError, match="Invalid effect"):
            parser.parse_string(yaml)

    def test_rule_conditions_not_a_list(self, parser: PolicyParser):
        """conditions as a scalar raises PolicyValidationError."""
        yaml = """\
rules:
  - id: "bad-conds"
    effect: allow
    conditions: "oops"
"""
        with pytest.raises(PolicyParseError, match="conditions must be a list"):
            parser.parse_string(yaml)

    def test_rule_empty_conditions(self, parser: PolicyParser):
        """Empty conditions list raises PolicyValidationError."""
        yaml = """\
rules:
  - id: "empty-conds"
    effect: allow
    conditions: []
"""
        with pytest.raises(PolicyParseError, match="at least one condition"):
            parser.parse_string(yaml)

    def test_rule_name_defaults_to_id(self, parser: PolicyParser):
        """Rule name defaults to the id when not explicitly set."""
        yaml = """\
rules:
  - id: "my-rule"
    effect: allow
    conditions:
      - command: "ls"
"""
        doc = parser.parse_string(yaml)

        assert doc.rules[0].name == "my-rule"

    def test_rule_optional_fields_defaults(self, parser: PolicyParser):
        """Optional fields receive their correct defaults."""
        yaml = """\
rules:
  - id: "defaults-rule"
    effect: allow
    conditions:
      - command: "ls"
"""
        doc = parser.parse_string(yaml)
        rule = doc.rules[0]

        assert rule.match_all is True
        assert rule.message is None
        assert rule.justification_prompt is None
        assert rule.priority == 0
        assert rule.enabled is True

    def test_rule_with_all_optional_fields(self, parser: PolicyParser):
        """All optional rule fields are parsed properly."""
        yaml = """\
rules:
  - id: "full-rule"
    name: "Full Rule"
    effect: require_justification
    match_all: false
    message: "custom message"
    justification_prompt: "why?"
    priority: 42
    enabled: false
    conditions:
      - command: "rm"
"""
        doc = parser.parse_string(yaml)
        rule = doc.rules[0]

        assert rule.match_all is False
        assert rule.message == "custom message"
        assert rule.justification_prompt == "why?"
        assert rule.priority == 42
        assert rule.enabled is False


# =========================================================================
# TestParseCondition
# =========================================================================


class TestParseCondition:
    """Tests for PolicyParser._parse_condition."""

    def _make_yaml(self, condition_body: str) -> str:
        return f"""\
rules:
  - id: "cond-rule"
    effect: allow
    conditions:
      - {condition_body}
"""

    def test_condition_not_a_dict(self, parser: PolicyParser):
        """Non-dict condition raises PolicyValidationError."""
        yaml = """\
rules:
  - id: "bad-cond"
    effect: allow
    conditions:
      - "just a string"
"""
        with pytest.raises(PolicyParseError, match="must be a mapping"):
            parser.parse_string(yaml)

    def test_condition_no_matching_field(self, parser: PolicyParser):
        """Condition with no pattern or attribute raises PolicyValidationError."""
        yaml = """\
rules:
  - id: "no-fields"
    effect: allow
    conditions:
      - negate: true
"""
        with pytest.raises(PolicyParseError, match="at least one matching field"):
            parser.parse_string(yaml)

    def test_condition_command_only(self, parser: PolicyParser):
        """Condition with just a command pattern."""
        yaml = self._make_yaml('command: "ls *"\n        match_type: glob')
        doc = parser.parse_string(yaml)
        cond = doc.rules[0].conditions[0]

        assert cond.command == "ls *"
        assert cond.match_type == MatchType.GLOB
        assert cond.path is None
        assert cond.domain is None
        assert cond.intent is None

    def test_condition_path_only(self, parser: PolicyParser):
        """Condition with just a path pattern."""
        yaml = self._make_yaml('path: "/etc/**"\n        match_type: glob')
        doc = parser.parse_string(yaml)
        cond = doc.rules[0].conditions[0]

        assert cond.path == "/etc/**"

    def test_condition_domain_only(self, parser: PolicyParser):
        """Condition with just a domain pattern."""
        yaml = self._make_yaml('domain: "network.*"')
        doc = parser.parse_string(yaml)
        cond = doc.rules[0].conditions[0]

        assert cond.domain == "network.*"

    def test_condition_intent_only(self, parser: PolicyParser):
        """Condition with just an intent."""
        yaml = self._make_yaml('intent: "shell_passthrough"')
        doc = parser.parse_string(yaml)
        cond = doc.rules[0].conditions[0]

        assert cond.intent == "shell_passthrough"

    def test_condition_match_type_exact(self, parser: PolicyParser):
        """match_type exact parses correctly."""
        yaml = self._make_yaml('command: "ls"\n        match_type: exact')
        doc = parser.parse_string(yaml)

        assert doc.rules[0].conditions[0].match_type == MatchType.EXACT

    def test_condition_match_type_regex(self, parser: PolicyParser):
        """match_type regex parses correctly."""
        yaml = self._make_yaml('command: "^rm.*"\n        match_type: regex')
        doc = parser.parse_string(yaml)

        assert doc.rules[0].conditions[0].match_type == MatchType.REGEX

    def test_condition_match_type_prefix(self, parser: PolicyParser):
        """match_type prefix parses correctly."""
        yaml = self._make_yaml('command: "sudo "\n        match_type: prefix')
        doc = parser.parse_string(yaml)

        assert doc.rules[0].conditions[0].match_type == MatchType.PREFIX

    def test_condition_invalid_match_type(self, parser: PolicyParser):
        """Invalid match_type raises PolicyValidationError."""
        yaml = self._make_yaml('command: "ls"\n        match_type: fuzzy')
        with pytest.raises(PolicyParseError, match="Invalid match_type"):
            parser.parse_string(yaml)

    def test_condition_match_type_default_is_glob(self, parser: PolicyParser):
        """Default match_type is glob when omitted."""
        yaml = self._make_yaml('command: "ls *"')
        doc = parser.parse_string(yaml)

        assert doc.rules[0].conditions[0].match_type == MatchType.GLOB

    def test_condition_risk_level_low(self, parser: PolicyParser):
        """risk_level: low parses correctly."""
        yaml = self._make_yaml("risk_level: low")
        doc = parser.parse_string(yaml)

        assert doc.rules[0].conditions[0].risk_level == RiskLevel.LOW

    def test_condition_risk_level_medium(self, parser: PolicyParser):
        """risk_level: medium parses correctly."""
        yaml = self._make_yaml("risk_level: medium")
        doc = parser.parse_string(yaml)

        assert doc.rules[0].conditions[0].risk_level == RiskLevel.MEDIUM

    def test_condition_risk_level_high(self, parser: PolicyParser):
        """risk_level: high parses correctly."""
        yaml = self._make_yaml("risk_level: high")
        doc = parser.parse_string(yaml)

        assert doc.rules[0].conditions[0].risk_level == RiskLevel.HIGH

    def test_condition_invalid_risk_level(self, parser: PolicyParser):
        """Invalid risk_level raises PolicyValidationError."""
        yaml = self._make_yaml("risk_level: catastrophic")
        with pytest.raises(PolicyParseError, match="Invalid risk_level"):
            parser.parse_string(yaml)

    def test_condition_requires_privilege_true(self, parser: PolicyParser):
        """requires_privilege: true parses correctly."""
        yaml = self._make_yaml("requires_privilege: true")
        doc = parser.parse_string(yaml)

        assert doc.rules[0].conditions[0].requires_privilege is True

    def test_condition_requires_privilege_false(self, parser: PolicyParser):
        """requires_privilege: false parses correctly (not None)."""
        yaml = self._make_yaml("requires_privilege: false")
        doc = parser.parse_string(yaml)

        assert doc.rules[0].conditions[0].requires_privilege is False

    def test_condition_requires_privilege_absent(self, parser: PolicyParser):
        """Absent requires_privilege stays None."""
        yaml = self._make_yaml('command: "ls"')
        doc = parser.parse_string(yaml)

        assert doc.rules[0].conditions[0].requires_privilege is None

    def test_condition_negate_true(self, parser: PolicyParser):
        """negate: true is parsed."""
        yaml = self._make_yaml('command: "safe"\n        negate: true')
        doc = parser.parse_string(yaml)

        assert doc.rules[0].conditions[0].negate is True

    def test_condition_negate_default_false(self, parser: PolicyParser):
        """Default negate is False."""
        yaml = self._make_yaml('command: "ls"')
        doc = parser.parse_string(yaml)

        assert doc.rules[0].conditions[0].negate is False


# =========================================================================
# TestParseConditionWithTimeWindows
# =========================================================================


class TestParseConditionTimeWindows:
    """Tests for time-window parsing inside conditions."""

    def test_single_time_window(self, parser: PolicyParser):
        """Condition with a single allowed_hours window."""
        yaml = """\
rules:
  - id: "time-rule"
    effect: allow
    conditions:
      - command: "deploy"
        allowed_hours:
          - start: "09:00"
            end: "17:00"
"""
        doc = parser.parse_string(yaml)
        cond = doc.rules[0].conditions[0]

        assert cond.allowed_hours is not None
        assert len(cond.allowed_hours) == 1
        assert cond.allowed_hours[0].start == time(9, 0)
        assert cond.allowed_hours[0].end == time(17, 0)

    def test_multiple_time_windows(self, parser: PolicyParser):
        """Condition with multiple allowed_hours windows."""
        yaml = """\
rules:
  - id: "time-rule"
    effect: allow
    conditions:
      - command: "deploy"
        allowed_hours:
          - start: "02:00"
            end: "05:00"
          - start: "22:00"
            end: "23:59"
"""
        doc = parser.parse_string(yaml)
        cond = doc.rules[0].conditions[0]

        assert cond.allowed_hours is not None
        assert len(cond.allowed_hours) == 2
        assert cond.allowed_hours[0].start == time(2, 0)
        assert cond.allowed_hours[1].start == time(22, 0)

    def test_time_window_midnight_boundary(self, parser: PolicyParser):
        """Time window that wraps around midnight."""
        yaml = """\
rules:
  - id: "midnight-rule"
    effect: allow
    conditions:
      - command: "backup"
        allowed_hours:
          - start: "23:00"
            end: "01:00"
"""
        doc = parser.parse_string(yaml)
        cond = doc.rules[0].conditions[0]

        assert cond.allowed_hours[0].start == time(23, 0)
        assert cond.allowed_hours[0].end == time(1, 0)

    def test_no_allowed_hours_yields_none(self, parser: PolicyParser):
        """Absent allowed_hours stays None."""
        yaml = """\
rules:
  - id: "no-time"
    effect: allow
    conditions:
      - command: "ls"
"""
        doc = parser.parse_string(yaml)

        assert doc.rules[0].conditions[0].allowed_hours is None


# =========================================================================
# TestParseTimeWindow
# =========================================================================


class TestParseTimeWindow:
    """Tests for PolicyParser._parse_time_window."""

    def test_missing_start(self, parser: PolicyParser):
        """Time window without start raises PolicyValidationError."""
        yaml = """\
rules:
  - id: "tw-rule"
    effect: allow
    conditions:
      - command: "deploy"
        allowed_hours:
          - end: "17:00"
"""
        with pytest.raises(PolicyParseError, match="must have 'start' and 'end'"):
            parser.parse_string(yaml)

    def test_missing_end(self, parser: PolicyParser):
        """Time window without end raises PolicyValidationError."""
        yaml = """\
rules:
  - id: "tw-rule"
    effect: allow
    conditions:
      - command: "deploy"
        allowed_hours:
          - start: "09:00"
"""
        with pytest.raises(PolicyParseError, match="must have 'start' and 'end'"):
            parser.parse_string(yaml)


# =========================================================================
# TestParseTime
# =========================================================================


class TestParseTime:
    """Tests for PolicyParser._parse_time (HH:MM parser)."""

    def test_valid_times(self, parser: PolicyParser):
        """Various valid HH:MM strings parse correctly."""
        assert parser._parse_time("00:00") == time(0, 0)
        assert parser._parse_time("09:30") == time(9, 30)
        assert parser._parse_time("12:00") == time(12, 0)
        assert parser._parse_time("23:59") == time(23, 59)

    def test_no_colon(self, parser: PolicyParser):
        """String without colon raises ValueError."""
        with pytest.raises(ValueError, match="Expected HH:MM"):
            parser._parse_time("0930")

    def test_too_many_parts(self, parser: PolicyParser):
        """String with extra colons raises ValueError."""
        with pytest.raises(ValueError, match="Expected HH:MM"):
            parser._parse_time("09:30:00")

    def test_hour_too_large(self, parser: PolicyParser):
        """Hour > 23 raises ValueError."""
        with pytest.raises(ValueError, match="Hour must be 0-23"):
            parser._parse_time("24:00")

    def test_hour_negative(self, parser: PolicyParser):
        """Negative hour raises ValueError."""
        with pytest.raises(ValueError, match="Hour must be 0-23"):
            parser._parse_time("-1:00")

    def test_minute_too_large(self, parser: PolicyParser):
        """Minute > 59 raises ValueError."""
        with pytest.raises(ValueError, match="Minute must be 0-59"):
            parser._parse_time("12:60")

    def test_minute_negative(self, parser: PolicyParser):
        """Negative minute raises ValueError."""
        with pytest.raises(ValueError, match="Minute must be 0-59"):
            parser._parse_time("12:-1")

    def test_non_numeric_raises(self, parser: PolicyParser):
        """Non-numeric components raise ValueError."""
        with pytest.raises(ValueError):
            parser._parse_time("ab:cd")

    def test_invalid_time_in_window_raises_parse_error(self, parser: PolicyParser):
        """Invalid time format inside a time window raises PolicyParseError."""
        yaml = """\
rules:
  - id: "bad-time"
    effect: allow
    conditions:
      - command: "deploy"
        allowed_hours:
          - start: "25:00"
            end: "17:00"
"""
        with pytest.raises(PolicyParseError, match="Invalid time format"):
            parser.parse_string(yaml)


# =========================================================================
# TestParseDocument -- edge cases
# =========================================================================


class TestParseDocumentEdgeCases:
    """Edge-case tests for _parse_document."""

    def test_version_coerced_to_string(self, parser: PolicyParser):
        """Numeric version is coerced to string."""
        yaml = """\
version: 2
rules: []
"""
        doc = parser.parse_string(yaml)

        assert doc.version == "2"

    def test_name_coerced_to_string(self, parser: PolicyParser):
        """Non-string name is coerced to string."""
        yaml = """\
name: 123
rules: []
"""
        doc = parser.parse_string(yaml)

        assert doc.name == "123"

    def test_extends_list_elements_coerced(self, parser: PolicyParser):
        """Elements in extends list are coerced to strings."""
        yaml = """\
extends:
  - 1
  - true
  - "real.yaml"
rules: []
"""
        doc = parser.parse_string(yaml)

        assert doc.extends == ("1", "True", "real.yaml")

    def test_rules_validation_error_wrapped(self, parser: PolicyParser):
        """A PolicyValidationError from a rule is re-raised as PolicyParseError."""
        yaml = """\
rules:
  - id: "bad"
    effect: allow
    conditions: []
"""
        with pytest.raises(PolicyParseError):
            parser.parse_string(yaml)

    def test_all_effect_values_accepted(self, parser: PolicyParser):
        """Every PolicyEffect value is accepted as default_effect."""
        for effect in PolicyEffect:
            yaml = f"""\
default_effect: {effect.value}
rules: []
"""
            doc = parser.parse_string(yaml)
            assert doc.default_effect == effect


# =========================================================================
# TestModuleLevelFunctions
# =========================================================================


class TestModuleLevelFunctions:
    """Tests for get_parser, parse_file, parse_string convenience functions."""

    def test_get_parser_returns_policy_parser(self):
        """get_parser returns a PolicyParser instance."""
        import elle.policy.parser as parser_mod

        # Reset the module-level singleton
        original = parser_mod._parser
        try:
            parser_mod._parser = None
            p = get_parser()
            assert isinstance(p, PolicyParser)
        finally:
            parser_mod._parser = original

    def test_get_parser_returns_same_instance(self):
        """get_parser returns the same singleton on subsequent calls."""
        import elle.policy.parser as parser_mod

        original = parser_mod._parser
        try:
            parser_mod._parser = None
            p1 = get_parser()
            p2 = get_parser()
            assert p1 is p2
        finally:
            parser_mod._parser = original

    def test_parse_string_convenience(self):
        """Module-level parse_string delegates to the shared parser."""
        doc = parse_string(MINIMAL_YAML, source="test")

        assert isinstance(doc, PolicyDocument)
        assert doc.name == "Minimal Policy"

    def test_parse_file_convenience(self, tmp_path: Path):
        """Module-level parse_file delegates to the shared parser."""
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(MINIMAL_YAML, encoding="utf-8")

        doc = parse_file(policy_file)

        assert isinstance(doc, PolicyDocument)
        assert doc.name == "Minimal Policy"

    def test_parse_file_convenience_not_found(self):
        """Module-level parse_file raises PolicyParseError for missing files."""
        with pytest.raises(PolicyParseError, match="File not found"):
            parse_file("/no/such/file.yaml")


# =========================================================================
# TestComplexPolicies -- integration-style parse round-trips
# =========================================================================


class TestComplexPolicies:
    """Integration-style tests parsing realistic policies."""

    def test_policy_with_risk_level_and_privilege(self, parser: PolicyParser):
        """Policy combining risk_level and requires_privilege conditions."""
        yaml = """\
version: "1.0"
name: "Risk Policy"
default_effect: allow
rules:
  - id: "deny-high-risk-priv"
    effect: deny
    match_all: true
    conditions:
      - risk_level: high
      - requires_privilege: true
"""
        doc = parser.parse_string(yaml)

        assert len(doc.rules) == 1
        conds = doc.rules[0].conditions
        assert len(conds) == 2
        assert conds[0].risk_level == RiskLevel.HIGH
        assert conds[1].requires_privilege is True

    def test_policy_with_time_windows_and_negate(self, parser: PolicyParser):
        """Policy with time windows and negation."""
        yaml = """\
version: "1.0"
name: "Time Negate Policy"
default_effect: deny
rules:
  - id: "allow-business-hours"
    effect: allow
    conditions:
      - command: "*"
        allowed_hours:
          - start: "09:00"
            end: "17:00"
        negate: false
"""
        doc = parser.parse_string(yaml)
        cond = doc.rules[0].conditions[0]

        assert cond.allowed_hours is not None
        assert cond.negate is False

    def test_policy_all_match_types(self, parser: PolicyParser):
        """Each MatchType value is accepted in conditions."""
        for mt in MatchType:
            yaml = f"""\
rules:
  - id: "mt-{mt.value}"
    effect: allow
    conditions:
      - command: "test"
        match_type: {mt.value}
"""
            doc = parser.parse_string(yaml)
            assert doc.rules[0].conditions[0].match_type == mt

    def test_policy_multiple_rules_with_multiple_conditions(self, parser: PolicyParser):
        """Multiple rules each with multiple conditions."""
        yaml = """\
version: "1.0"
name: "Multi Rule"
default_effect: deny
rules:
  - id: "rule-1"
    effect: allow
    match_all: false
    conditions:
      - command: "ls"
      - command: "cat"
  - id: "rule-2"
    effect: deny
    match_all: true
    conditions:
      - domain: "network.*"
      - risk_level: high
"""
        doc = parser.parse_string(yaml)

        assert len(doc.rules) == 2
        assert len(doc.rules[0].conditions) == 2
        assert len(doc.rules[1].conditions) == 2
        assert doc.rules[0].match_all is False
        assert doc.rules[1].match_all is True

    def test_empty_content_string(self, parser: PolicyParser):
        """Empty string (parses to None) raises PolicyParseError."""
        with pytest.raises(PolicyParseError, match="must be a YAML mapping"):
            parser.parse_string("")

    def test_null_yaml_content(self, parser: PolicyParser):
        """YAML document consisting only of 'null' raises PolicyParseError."""
        with pytest.raises(PolicyParseError, match="must be a YAML mapping"):
            parser.parse_string("null")

    def test_condition_attribute_only_no_pattern(self, parser: PolicyParser):
        """Condition with only an attribute field (risk_level) and no pattern is valid."""
        yaml = """\
rules:
  - id: "attr-only"
    effect: deny
    conditions:
      - risk_level: high
"""
        doc = parser.parse_string(yaml)

        assert doc.rules[0].conditions[0].risk_level == RiskLevel.HIGH
        assert doc.rules[0].conditions[0].command is None

    def test_condition_allowed_hours_only(self, parser: PolicyParser):
        """Condition with only allowed_hours is valid (it is an attribute)."""
        yaml = """\
rules:
  - id: "hours-only"
    effect: allow
    conditions:
      - allowed_hours:
          - start: "08:00"
            end: "20:00"
"""
        doc = parser.parse_string(yaml)
        cond = doc.rules[0].conditions[0]

        assert cond.allowed_hours is not None
        assert len(cond.allowed_hours) == 1
