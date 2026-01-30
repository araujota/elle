from __future__ import annotations

"""Tests for capabilities_commands.py - capability browsing and autonomy commands."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Helpers for building mock objects
# ---------------------------------------------------------------------------

def _make_cap_spec(
    name: str = "service.restart",
    domain: str = "service",
    risk: str = "low",
    summary: str = "Restart a service",
    requires_privilege: bool = False,
    trust_level: str = "core",
    side_effects: tuple = (),
    dependencies: tuple = (),
):
    """Build a CapabilitySpec-like object."""
    return SimpleNamespace(
        name=name,
        domain=domain,
        risk=risk,
        summary=summary,
        requires_privilege=requires_privilege,
        trust_level=trust_level,
        side_effects=side_effects,
        dependencies=dependencies,
    )


def _make_autonomy_status(
    capability_name: str = "service.restart",
    can_run: bool = True,
    source: str = "base_level",
    reason: str = "Within risk threshold",
    earned_progress: float | None = None,
    stats=None,
):
    return SimpleNamespace(
        capability_name=capability_name,
        can_run_autonomously=can_run,
        source=source,
        reason=reason,
        earned_progress=earned_progress,
        stats=stats,
    )


def _make_exec_stats(
    name: str = "service.restart",
    total: int = 10,
    success: int = 9,
    failed: int = 1,
    last_at=None,
):
    return SimpleNamespace(
        capability_name=name,
        total_executions=total,
        successful_executions=success,
        failed_executions=failed,
        success_rate=success / total if total else 0.0,
        last_executed_at=last_at,
    )


def _make_earned_autonomy(enabled: bool = True, min_executions: int = 10, min_success_rate: float = 0.95, cooldown: int = 5):
    return SimpleNamespace(
        enabled=enabled,
        min_executions=min_executions,
        min_success_rate=min_success_rate,
        cooldown_after_failure=cooldown,
    )


def _make_prefs(base_level_value: str = "low_risk", earned_enabled: bool = True):
    level = SimpleNamespace(value=base_level_value)
    earned = _make_earned_autonomy(enabled=earned_enabled)
    return SimpleNamespace(
        base_level=level,
        earned_autonomy=earned,
    )


def _make_session():
    """Return a minimal real session."""
    from elle.common.session import create_session
    return create_session()


# ---------------------------------------------------------------------------
# risk_text / risk_bar / format_time_ago / _progress_bar helpers
# ---------------------------------------------------------------------------


class TestRiskText:
    def test_known_risk_level(self):
        from elle.cli.capabilities_commands import risk_text
        result = risk_text("low")
        assert result.plain == "low"

    def test_unknown_risk_level_uses_white(self):
        from elle.cli.capabilities_commands import risk_text
        result = risk_text("unknown_risk")
        assert result.plain == "unknown_risk"


class TestRiskBar:
    def test_empty_counts(self):
        from elle.cli.capabilities_commands import risk_bar
        bar = risk_bar({})
        assert bar.plain == "----------"

    def test_all_low(self):
        from elle.cli.capabilities_commands import risk_bar
        bar = risk_bar({"low": 10})
        # Should produce a non-empty result (spaces used as visual blocks)
        assert len(bar.plain) > 0

    def test_mixed_risks(self):
        from elle.cli.capabilities_commands import risk_bar
        bar = risk_bar({"low": 5, "high": 5})
        assert len(bar.plain) > 0


class TestFormatTimeAgo:
    def test_none_returns_never(self):
        from elle.cli.capabilities_commands import format_time_ago
        assert format_time_ago(None) == "never"

    def test_just_now(self):
        from elle.cli.capabilities_commands import format_time_ago
        result = format_time_ago(datetime.utcnow() - timedelta(seconds=10))
        assert result == "just now"

    def test_minutes_ago(self):
        from elle.cli.capabilities_commands import format_time_ago
        result = format_time_ago(datetime.utcnow() - timedelta(minutes=5))
        assert "m ago" in result

    def test_hours_ago(self):
        from elle.cli.capabilities_commands import format_time_ago
        result = format_time_ago(datetime.utcnow() - timedelta(hours=3))
        assert "h ago" in result

    def test_days_ago(self):
        from elle.cli.capabilities_commands import format_time_ago
        result = format_time_ago(datetime.utcnow() - timedelta(days=3))
        assert "d ago" in result

    def test_old_date_formatted(self):
        from elle.cli.capabilities_commands import format_time_ago
        old = datetime(2020, 1, 15)
        result = format_time_ago(old)
        assert "2020-01-15" in result


class TestProgressBar:
    def test_zero_progress(self):
        from elle.cli.capabilities_commands import _progress_bar
        assert _progress_bar(0.0) == "[          ]"

    def test_full_progress(self):
        from elle.cli.capabilities_commands import _progress_bar
        assert _progress_bar(1.0) == "[||||||||||]"

    def test_half_progress(self):
        from elle.cli.capabilities_commands import _progress_bar
        result = _progress_bar(0.5)
        assert "|" in result
        assert " " in result


class TestGetDomainDescription:
    def test_known_domain(self):
        from elle.cli.capabilities_commands import _get_domain_description
        assert "service" in _get_domain_description("service").lower() or "systemd" in _get_domain_description("service").lower()

    def test_unknown_domain(self):
        from elle.cli.capabilities_commands import _get_domain_description
        result = _get_domain_description("custom")
        assert "custom" in result


class TestLevelDescription:
    def test_known_levels(self):
        from elle.cli.capabilities_commands import _level_description, AutonomyLevel
        result = _level_description(AutonomyLevel.READONLY)
        assert "none" in result

    def test_full_auto(self):
        from elle.cli.capabilities_commands import _level_description, AutonomyLevel
        result = _level_description(AutonomyLevel.FULL_AUTO)
        assert "all" in result.lower()


# ---------------------------------------------------------------------------
# Autonomy badge helper
# ---------------------------------------------------------------------------


class TestAutonomyBadge:
    def test_earned_badge(self):
        from elle.cli.capabilities_commands import autonomy_badge
        status = _make_autonomy_status(can_run=True, source="earned")
        badge = autonomy_badge(status)
        assert badge.plain == "earned"

    def test_manual_grant_badge(self):
        from elle.cli.capabilities_commands import autonomy_badge
        status = _make_autonomy_status(can_run=True, source="manual_grant")
        badge = autonomy_badge(status)
        assert badge.plain == "granted"

    def test_base_level_badge(self):
        from elle.cli.capabilities_commands import autonomy_badge
        status = _make_autonomy_status(can_run=True, source="base_level")
        badge = autonomy_badge(status)
        assert badge.plain == "auto"

    def test_manual_revoke_badge(self):
        from elle.cli.capabilities_commands import autonomy_badge
        status = _make_autonomy_status(can_run=False, source="manual_revoke")
        badge = autonomy_badge(status)
        assert badge.plain == "blocked"

    def test_confirm_badge(self):
        from elle.cli.capabilities_commands import autonomy_badge
        status = _make_autonomy_status(can_run=False, source="risk_blocked")
        badge = autonomy_badge(status)
        assert badge.plain == "confirm"


# ---------------------------------------------------------------------------
# _render_search_results
# ---------------------------------------------------------------------------


class TestRenderSearchResults:
    @patch("elle.cli.capabilities_commands.get_registry")
    def test_no_results(self, mock_get_reg):
        from elle.cli.capabilities_commands import _render_search_results
        mock_get_reg.return_value.search.return_value = []
        result = _render_search_results("nonexistent")
        assert "No capabilities found" in result

    @patch("elle.cli.capabilities_commands.get_registry")
    def test_some_results(self, mock_get_reg):
        from elle.cli.capabilities_commands import _render_search_results
        caps = [_make_cap_spec(name=f"service.op{i}", summary=f"Op {i}") for i in range(3)]
        mock_get_reg.return_value.search.return_value = caps
        result = _render_search_results("service")
        assert "Found 3" in result

    @patch("elle.cli.capabilities_commands.get_registry")
    def test_more_than_twenty_truncated(self, mock_get_reg):
        from elle.cli.capabilities_commands import _render_search_results
        caps = [_make_cap_spec(name=f"x.op{i}", summary=f"Op {i}") for i in range(25)]
        mock_get_reg.return_value.search.return_value = caps
        result = _render_search_results("x")
        assert "and 5 more" in result


# ---------------------------------------------------------------------------
# handle_capabilities_command (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHandleCapabilitiesCommand:
    @patch("elle.cli.capabilities_commands._render_domain_summary", return_value="DOMAIN_SUMMARY")
    async def test_no_args_shows_summary(self, mock_render):
        from elle.cli.capabilities_commands import handle_capabilities_command
        result = await handle_capabilities_command("/capabilities", _make_session())
        assert result.output == "DOMAIN_SUMMARY"
        assert result.success is True

    @patch("elle.cli.capabilities_commands._render_domain_summary", return_value="DOMAIN_SUMMARY")
    async def test_cap_alias(self, mock_render):
        from elle.cli.capabilities_commands import handle_capabilities_command
        result = await handle_capabilities_command("/cap", _make_session())
        assert result.output == "DOMAIN_SUMMARY"

    @patch("elle.cli.capabilities_commands._render_search_results", return_value="SEARCH")
    async def test_search_subcommand(self, mock_render):
        from elle.cli.capabilities_commands import handle_capabilities_command
        result = await handle_capabilities_command("/capabilities search restart", _make_session())
        assert result.output == "SEARCH"
        mock_render.assert_called_once_with("restart")

    @patch("elle.cli.capabilities_commands.get_registry")
    @patch("elle.cli.capabilities_commands._render_domain_capabilities", return_value="DOMAIN_CAPS")
    async def test_domain_match(self, mock_render, mock_get_reg):
        from elle.cli.capabilities_commands import handle_capabilities_command
        mock_get_reg.return_value.list_domains.return_value = ["service", "file"]
        result = await handle_capabilities_command("/capabilities service", _make_session())
        assert result.output == "DOMAIN_CAPS"

    @patch("elle.cli.capabilities_commands.get_registry")
    @patch("elle.cli.capabilities_commands._render_capability_detail", return_value="CAP_DETAIL")
    async def test_full_capability_name(self, mock_detail, mock_get_reg):
        from elle.cli.capabilities_commands import handle_capabilities_command
        mock_get_reg.return_value.list_domains.return_value = []
        cap_obj = MagicMock()
        cap_obj.spec = _make_cap_spec()
        mock_get_reg.return_value.get.return_value = cap_obj
        result = await handle_capabilities_command("/capabilities service.restart", _make_session())
        assert result.output == "CAP_DETAIL"

    @patch("elle.cli.capabilities_commands.get_registry")
    @patch("elle.cli.capabilities_commands._render_search_results", return_value="FALLBACK_SEARCH")
    async def test_unknown_arg_falls_back_to_search(self, mock_search, mock_get_reg):
        from elle.cli.capabilities_commands import handle_capabilities_command
        mock_get_reg.return_value.list_domains.return_value = []
        mock_get_reg.return_value.get.return_value = None
        result = await handle_capabilities_command("/capabilities foobar", _make_session())
        assert result.output == "FALLBACK_SEARCH"


# ---------------------------------------------------------------------------
# handle_autonomy_command (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHandleAutonomyCommand:
    @patch("elle.cli.capabilities_commands._render_autonomy_status", return_value="STATUS")
    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_no_args_shows_status(self, mock_engine, mock_render):
        from elle.cli.capabilities_commands import handle_autonomy_command
        result = await handle_autonomy_command("/autonomy", _make_session())
        assert result.output == "STATUS"

    @patch("elle.cli.capabilities_commands._render_autonomy_status", return_value="STATUS")
    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_status_subcommand(self, mock_engine, mock_render):
        from elle.cli.capabilities_commands import handle_autonomy_command
        result = await handle_autonomy_command("/autonomy status", _make_session())
        assert result.output == "STATUS"

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_set_valid_level(self, mock_engine):
        from elle.cli.capabilities_commands import handle_autonomy_command
        result = await handle_autonomy_command("/autonomy set low_risk", _make_session())
        assert "level set to" in result.output

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_set_invalid_level(self, mock_engine):
        from elle.cli.capabilities_commands import handle_autonomy_command
        result = await handle_autonomy_command("/autonomy set banana", _make_session())
        assert "Invalid level" in result.output

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_earn_on(self, mock_engine):
        from elle.cli.capabilities_commands import handle_autonomy_command
        result = await handle_autonomy_command("/autonomy earn on", _make_session())
        assert "enabled" in result.output

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_earn_off(self, mock_engine):
        from elle.cli.capabilities_commands import handle_autonomy_command
        result = await handle_autonomy_command("/autonomy earn off", _make_session())
        assert "disabled" in result.output

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_earn_invalid(self, mock_engine):
        from elle.cli.capabilities_commands import handle_autonomy_command
        result = await handle_autonomy_command("/autonomy earn banana", _make_session())
        assert "Invalid value" in result.output

    @patch("elle.cli.capabilities_commands.get_registry")
    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_grant_existing_cap(self, mock_engine, mock_reg):
        from elle.cli.capabilities_commands import handle_autonomy_command
        mock_reg.return_value.get.return_value = MagicMock()
        result = await handle_autonomy_command("/autonomy grant service.restart", _make_session())
        assert "Granted" in result.output

    @patch("elle.cli.capabilities_commands.get_registry")
    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_grant_nonexistent_cap(self, mock_engine, mock_reg):
        from elle.cli.capabilities_commands import handle_autonomy_command
        mock_reg.return_value.get.return_value = None
        result = await handle_autonomy_command("/autonomy grant nosuchcap", _make_session())
        assert "not found" in result.output

    @patch("elle.cli.capabilities_commands.get_registry")
    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_revoke_existing_cap(self, mock_engine, mock_reg):
        from elle.cli.capabilities_commands import handle_autonomy_command
        mock_reg.return_value.get.return_value = MagicMock()
        result = await handle_autonomy_command("/autonomy revoke service.restart", _make_session())
        assert "Revoked" in result.output

    @patch("elle.cli.capabilities_commands._render_overrides", return_value="OVERRIDES")
    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_overrides_subcommand(self, mock_engine, mock_render):
        from elle.cli.capabilities_commands import handle_autonomy_command
        result = await handle_autonomy_command("/autonomy overrides", _make_session())
        assert result.output == "OVERRIDES"

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_unknown_subcommand_shows_help(self, mock_engine):
        from elle.cli.capabilities_commands import handle_autonomy_command
        result = await handle_autonomy_command("/autonomy blah", _make_session())
        assert "Autonomy Configuration Commands" in result.output


# ---------------------------------------------------------------------------
# _render_overrides
# ---------------------------------------------------------------------------


class TestRenderOverrides:
    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    def test_no_overrides(self, mock_engine):
        from elle.cli.capabilities_commands import _render_overrides
        mock_engine.return_value.store.list_overrides.return_value = []
        result = _render_overrides()
        assert "No autonomy overrides" in result

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    def test_with_overrides(self, mock_engine):
        from elle.cli.capabilities_commands import _render_overrides
        override = SimpleNamespace(
            capability_name="service.restart",
            autonomous=True,
            reason="manual",
        )
        mock_engine.return_value.store.list_overrides.return_value = [override]
        result = _render_overrides()
        assert "service.restart" in result
        assert "granted" in result


# ---------------------------------------------------------------------------
# _render_autonomy_help
# ---------------------------------------------------------------------------


class TestRenderAutonomyHelp:
    def test_returns_help_text(self):
        from elle.cli.capabilities_commands import _render_autonomy_help
        result = _render_autonomy_help()
        assert "/autonomy" in result
        assert "set" in result
        assert "earn" in result


# ---------------------------------------------------------------------------
# handle_capability_query (NL routing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHandleCapabilityQuery:
    @patch("elle.cli.capabilities_commands._render_domain_summary", return_value="SUMMARY")
    async def test_what_can_you_do(self, mock_render):
        from elle.cli.capabilities_commands import handle_capability_query
        result = await handle_capability_query("what can you do", _make_session())
        assert result.output == "SUMMARY"

    @patch("elle.cli.capabilities_commands._render_autonomy_status", return_value="AUTO_STATUS")
    async def test_autonomy_question(self, mock_render):
        from elle.cli.capabilities_commands import handle_capability_query
        result = await handle_capability_query("what can run automatically", _make_session())
        assert result.output == "AUTO_STATUS"

    @patch("elle.cli.capabilities_commands.get_registry")
    @patch("elle.cli.capabilities_commands._render_domain_capabilities", return_value="SERVICE_CAPS")
    async def test_domain_specific_question(self, mock_render, mock_reg):
        from elle.cli.capabilities_commands import handle_capability_query
        mock_reg.return_value.list_domains.return_value = ["service"]
        result = await handle_capability_query("service capabilities", _make_session())
        assert result.output == "SERVICE_CAPS"

    @patch("elle.cli.capabilities_commands.get_registry")
    @patch("elle.cli.capabilities_commands._render_search_results", return_value="SEARCH_RES")
    async def test_fallback_search(self, mock_search, mock_reg):
        from elle.cli.capabilities_commands import handle_capability_query
        mock_reg.return_value.list_domains.return_value = []
        result = await handle_capability_query("tell me about docker prune", _make_session())
        assert result.output == "SEARCH_RES"

    @patch("elle.cli.capabilities_commands.get_registry")
    @patch("elle.cli.capabilities_commands._render_domain_summary", return_value="SUMMARY")
    async def test_no_useful_search_terms_shows_summary(self, mock_summary, mock_reg):
        from elle.cli.capabilities_commands import handle_capability_query
        mock_reg.return_value.list_domains.return_value = []
        result = await handle_capability_query("what can you do about the capability", _make_session())
        assert result.output == "SUMMARY"
