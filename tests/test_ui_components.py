"""Tests for elle.cli.ui.components -- Reusable UI component widgets."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from elle.cli.ui.components import (
    badge,
    bulleted_list,
    code_inline,
    command_display,
    confidence_bar,
    confirmation_prompt,
    domain_badge,
    empty_state,
    env_var_action_bar,
    env_var_line,
    env_var_panel,
    env_var_summary_panel,
    env_var_summary_table,
    format_duration,
    key_value,
    key_value_table,
    numbered_list,
    option_list,
    outcome_badge,
    path_display,
    percentage_bar,
    privilege_badge,
    risk_badge,
    section_header,
    section_rule,
    severity_badge,
    status_badge,
    time_ago,
    tip,
)

# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------


class TestBadge:
    def test_basic_badge(self):
        result = badge("test", "info")
        assert isinstance(result, Text)
        assert "[test]" in result.plain

    def test_badge_with_icon(self):
        result = badge("test", "info", icon="X")
        assert isinstance(result, Text)
        assert "X" in result.plain

    def test_badge_without_icon(self):
        result = badge("test")
        assert isinstance(result, Text)


class TestStatusBadge:
    @pytest.mark.parametrize("status", ["open", "mitigated", "resolved", "false_positive"])
    def test_known_statuses(self, status):
        result = status_badge(status)
        assert isinstance(result, Text)


class TestOutcomeBadge:
    @pytest.mark.parametrize("outcome", ["improved", "partial", "no_change", "worse"])
    def test_known_outcomes(self, outcome):
        result = outcome_badge(outcome)
        assert isinstance(result, Text)


class TestRiskBadge:
    @pytest.mark.parametrize("risk", ["low", "medium", "high"])
    def test_known_risks(self, risk):
        result = risk_badge(risk)
        assert isinstance(result, Text)


class TestSeverityBadge:
    @pytest.mark.parametrize("severity", ["info", "warning", "error", "critical"])
    def test_known_severities(self, severity):
        result = severity_badge(severity)
        assert isinstance(result, Text)


class TestDomainBadge:
    @pytest.mark.parametrize("domain", ["net", "disk", "oom", "docker", "auth", "pkg", "service", "fs"])
    def test_known_domains(self, domain):
        result = domain_badge(domain)
        assert isinstance(result, Text)

    def test_unknown_domain(self):
        result = domain_badge("unknown_domain")
        assert isinstance(result, Text)


class TestPrivilegeBadge:
    def test_required(self):
        result = privilege_badge(True)
        assert isinstance(result, Text)
        assert "required" in result.plain

    def test_not_required(self):
        result = privilege_badge(False)
        assert isinstance(result, Text)
        assert "no privilege" in result.plain


# ---------------------------------------------------------------------------
# Key/Value Display
# ---------------------------------------------------------------------------


class TestKeyValue:
    def test_basic(self):
        result = key_value("Name", "Value")
        assert isinstance(result, Text)
        assert "Name" in result.plain
        assert "Value" in result.plain


class TestKeyValueTable:
    def test_basic_table(self):
        result = key_value_table([("Key1", "Val1"), ("Key2", "Val2")])
        assert isinstance(result, Table)

    def test_with_title(self):
        result = key_value_table([("K", "V")], title="Metadata")
        assert isinstance(result, Table)


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


class TestBulletedList:
    def test_single_item(self):
        result = bulleted_list(["item1"])
        assert isinstance(result, Text)
        assert "item1" in result.plain

    def test_multiple_items(self):
        result = bulleted_list(["a", "b", "c"])
        assert isinstance(result, Text)

    def test_custom_bullet(self):
        result = bulleted_list(["x"], bullet="-")
        assert isinstance(result, Text)


class TestNumberedList:
    def test_basic(self):
        result = numbered_list(["first", "second"])
        assert isinstance(result, Text)
        assert "1." in result.plain

    def test_custom_start(self):
        result = numbered_list(["item"], start=5)
        assert isinstance(result, Text)
        assert "5." in result.plain


class TestOptionList:
    def test_basic(self):
        result = option_list([("a", "Option A"), ("b", "Option B")])
        assert isinstance(result, Text)
        assert "[a]" in result.plain

    def test_with_selection(self):
        result = option_list([("a", "Option A"), ("b", "Option B")], selected=0)
        assert isinstance(result, Text)

    def test_no_selection(self):
        result = option_list([("x", "desc")])
        assert isinstance(result, Text)


# ---------------------------------------------------------------------------
# Time Formatting
# ---------------------------------------------------------------------------


class TestTimeAgo:
    def test_just_now(self):
        assert time_ago(datetime.now()) == "just now"

    def test_minutes(self):
        dt = datetime.now() - timedelta(minutes=5)
        assert time_ago(dt) == "5m ago"

    def test_hours(self):
        dt = datetime.now() - timedelta(hours=3)
        assert time_ago(dt) == "3h ago"

    def test_days(self):
        dt = datetime.now() - timedelta(days=2)
        assert time_ago(dt) == "2d ago"

    def test_weeks(self):
        dt = datetime.now() - timedelta(weeks=2)
        assert time_ago(dt) == "2w ago"

    def test_months(self):
        dt = datetime.now() - timedelta(days=60)
        assert time_ago(dt) == "2mo ago"

    def test_years(self):
        dt = datetime.now() - timedelta(days=400)
        assert time_ago(dt) == "1y ago"


class TestFormatDuration:
    def test_seconds(self):
        assert format_duration(45) == "45s"

    def test_minutes(self):
        assert format_duration(120) == "2m"

    def test_minutes_and_seconds(self):
        assert format_duration(125) == "2m 5s"

    def test_hours(self):
        assert format_duration(3600) == "1h"

    def test_hours_and_minutes(self):
        assert format_duration(3660) == "1h 1m"


# ---------------------------------------------------------------------------
# Confidence / Percentage Bars
# ---------------------------------------------------------------------------


class TestConfidenceBar:
    def test_high_confidence(self):
        result = confidence_bar(0.9)
        assert isinstance(result, Text)
        assert "90%" in result.plain

    def test_medium_confidence(self):
        result = confidence_bar(0.6)
        assert isinstance(result, Text)

    def test_low_confidence(self):
        result = confidence_bar(0.2)
        assert isinstance(result, Text)

    def test_custom_width(self):
        result = confidence_bar(0.5, width=5)
        assert isinstance(result, Text)


class TestPercentageBar:
    def test_normal(self):
        result = percentage_bar(0.5)
        assert isinstance(result, Text)

    def test_warning_threshold(self):
        result = percentage_bar(0.75)
        assert isinstance(result, Text)

    def test_critical_threshold(self):
        result = percentage_bar(0.95)
        assert isinstance(result, Text)


# ---------------------------------------------------------------------------
# Section Headers
# ---------------------------------------------------------------------------


class TestSectionHeader:
    def test_basic(self):
        result = section_header("Title")
        assert isinstance(result, Text)
        assert "Title" in result.plain

    def test_with_icon(self):
        result = section_header("Title", icon="*")
        assert isinstance(result, Text)
        assert "*" in result.plain


class TestSectionRule:
    def test_basic(self):
        result = section_rule("divider")
        assert isinstance(result, Rule)

    def test_empty(self):
        result = section_rule()
        assert isinstance(result, Rule)


# ---------------------------------------------------------------------------
# Code / Command Display
# ---------------------------------------------------------------------------


class TestCodeInline:
    def test_basic(self):
        result = code_inline("echo hello")
        assert isinstance(result, Text)
        assert "echo hello" in result.plain


class TestCommandDisplay:
    def test_basic(self):
        result = command_display("ls -la")
        assert isinstance(result, Text)
        assert "$ " in result.plain
        assert "ls -la" in result.plain

    def test_custom_prefix(self):
        result = command_display("ls", prefix="#")
        assert isinstance(result, Text)
        assert "# " in result.plain


class TestPathDisplay:
    def test_basic(self):
        result = path_display("/etc/ssh/sshd_config")
        assert isinstance(result, Text)
        assert "/etc/ssh/sshd_config" in result.plain


# ---------------------------------------------------------------------------
# Compound Components
# ---------------------------------------------------------------------------


class TestConfirmationPrompt:
    def test_default_false(self):
        result = confirmation_prompt("Apply?")
        assert isinstance(result, Text)
        assert "[y/N]" in result.plain

    def test_default_true(self):
        result = confirmation_prompt("Apply?", default=True)
        assert isinstance(result, Text)
        assert "[Y/n]" in result.plain

    def test_with_risk(self):
        result = confirmation_prompt("Apply?", risk="high")
        assert isinstance(result, Text)


class TestTip:
    def test_basic(self):
        result = tip("use --verbose for more info")
        assert isinstance(result, Text)
        assert "Tip" in result.plain

    def test_custom_prefix(self):
        result = tip("try something", prefix="Hint")
        assert isinstance(result, Text)
        assert "Hint" in result.plain


class TestEmptyState:
    def test_basic(self):
        result = empty_state("No data available.")
        assert isinstance(result, Panel)

    def test_with_suggestion(self):
        result = empty_state("No incidents.", suggestion="Run a command first.")
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# Docker Environment Variable Components
# ---------------------------------------------------------------------------


class TestEnvVarLine:
    def test_basic(self):
        result = env_var_line("MY_VAR")
        assert isinstance(result, Text)
        assert "MY_VAR" in result.plain

    def test_all_flags(self):
        result = env_var_line(
            "DB_PASS",
            required=True,
            sensitive=True,
            default="admin",
            description="Database password",
            has_saved=True,
        )
        assert isinstance(result, Text)
        assert "required" in result.plain
        assert "sensitive" in result.plain
        assert "saved" in result.plain


class TestEnvVarPanel:
    def test_required_only(self):
        result = env_var_panel("postgres:15", [("POSTGRES_PASSWORD", "DB password", True)])
        assert isinstance(result, Panel)

    def test_with_optional(self):
        result = env_var_panel(
            "redis:7",
            [("REDIS_PASSWORD", None, True)],
            optional_vars=[("REDIS_LOG_LEVEL", "Log level", False)],
            saved_names={"REDIS_PASSWORD"},
        )
        assert isinstance(result, Panel)


class TestEnvVarSummaryTable:
    def test_basic(self):
        result = env_var_summary_table(
            [
                ("DB_HOST", "localhost", False, False),
                ("DB_PASS", "secret", True, True),
            ]
        )
        assert isinstance(result, Table)


class TestEnvVarSummaryPanel:
    def test_basic(self):
        result = env_var_summary_panel(
            [
                ("KEY", "value", False, False),
            ]
        )
        assert isinstance(result, Panel)


class TestEnvVarActionBar:
    def test_default_mode(self):
        result = env_var_action_bar()
        assert isinstance(result, Text)

    def test_with_saved(self):
        result = env_var_action_bar(has_saved=True)
        assert isinstance(result, Text)

    def test_confirm_mode(self):
        result = env_var_action_bar(show_confirm=True)
        assert isinstance(result, Text)
