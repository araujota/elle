"""Tests for capabilities_commands.py - capability browsing and autonomy commands."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

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


def _make_earned_autonomy(
    enabled: bool = True, min_executions: int = 10, min_success_rate: float = 0.95, cooldown: int = 5
):
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


def _make_prefs_enum(base_level=None, earned_enabled: bool = True):
    """Build prefs using actual AutonomyLevel enum for functions that need hashable values."""
    from elle.cli.capabilities_commands import AutonomyLevel

    if base_level is None:
        base_level = AutonomyLevel.LOW_RISK
    earned = _make_earned_autonomy(enabled=earned_enabled)
    return SimpleNamespace(
        base_level=base_level,
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

        assert (
            "service" in _get_domain_description("service").lower()
            or "systemd" in _get_domain_description("service").lower()
        )

    def test_unknown_domain(self):
        from elle.cli.capabilities_commands import _get_domain_description

        result = _get_domain_description("custom")
        assert "custom" in result


class TestLevelDescription:
    def test_known_levels(self):
        from elle.cli.capabilities_commands import AutonomyLevel, _level_description

        result = _level_description(AutonomyLevel.READONLY)
        assert "none" in result

    def test_full_auto(self):
        from elle.cli.capabilities_commands import AutonomyLevel, _level_description

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


# ---------------------------------------------------------------------------
# Extended coverage: _render_domain_summary
# ---------------------------------------------------------------------------


class TestRenderDomainSummary:
    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    @patch("elle.cli.capabilities_commands.get_registry")
    def test_renders_with_capabilities(self, mock_reg, mock_engine):
        from elle.cli.capabilities_commands import AutonomyLevel, _render_domain_summary

        caps = [
            _make_cap_spec(name="service.restart", domain="service", risk="low"),
            _make_cap_spec(name="service.start", domain="service", risk="medium"),
            _make_cap_spec(name="file.read", domain="file", risk="none"),
        ]
        mock_reg.return_value.list_all.return_value = caps
        mock_engine.return_value.store.get_preferences.return_value = _make_prefs_enum(
            AutonomyLevel.LOW_RISK
        )
        mock_engine.return_value.get_autonomy_status.return_value = _make_autonomy_status(
            can_run=True
        )

        result = _render_domain_summary()
        assert isinstance(result, str)
        # Should contain domain counts
        assert "3" in result or "service" in result.lower() or "Capabilities" in result

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    @patch("elle.cli.capabilities_commands.get_registry")
    def test_renders_empty_capabilities(self, mock_reg, mock_engine):
        from elle.cli.capabilities_commands import AutonomyLevel, _render_domain_summary

        mock_reg.return_value.list_all.return_value = []
        mock_engine.return_value.store.get_preferences.return_value = _make_prefs_enum(
            AutonomyLevel.LOW_RISK
        )

        result = _render_domain_summary()
        assert isinstance(result, str)
        assert "0" in result

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    @patch("elle.cli.capabilities_commands.get_registry")
    def test_renders_earned_autonomy_disabled(self, mock_reg, mock_engine):
        from elle.cli.capabilities_commands import AutonomyLevel, _render_domain_summary

        mock_reg.return_value.list_all.return_value = []
        mock_engine.return_value.store.get_preferences.return_value = _make_prefs_enum(
            AutonomyLevel.LOW_RISK, earned_enabled=False
        )

        result = _render_domain_summary()
        assert "disabled" in result


# ---------------------------------------------------------------------------
# Extended coverage: _render_domain_capabilities
# ---------------------------------------------------------------------------


class TestRenderDomainCapabilities:
    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    @patch("elle.cli.capabilities_commands.get_registry")
    def test_renders_empty_domain(self, mock_reg, mock_engine):
        from elle.cli.capabilities_commands import _render_domain_capabilities

        mock_reg.return_value.list_by_domain.return_value = []
        result = _render_domain_capabilities("nope")
        assert "No capabilities found" in result

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    @patch("elle.cli.capabilities_commands.get_registry")
    def test_renders_domain_with_stats(self, mock_reg, mock_engine):
        from elle.cli.capabilities_commands import _render_domain_capabilities

        caps = [
            _make_cap_spec(name="service.restart", domain="service", risk="low"),
            _make_cap_spec(name="service.start", domain="service", risk="medium"),
        ]
        mock_reg.return_value.list_by_domain.return_value = caps

        stats = _make_exec_stats(total=10, success=9, failed=1)
        mock_engine.return_value.get_autonomy_status.return_value = _make_autonomy_status(
            can_run=True, stats=stats
        )

        result = _render_domain_capabilities("service")
        assert isinstance(result, str)

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    @patch("elle.cli.capabilities_commands.get_registry")
    def test_renders_domain_with_zero_executions(self, mock_reg, mock_engine):
        from elle.cli.capabilities_commands import _render_domain_capabilities

        caps = [_make_cap_spec(name="service.restart", domain="service")]
        mock_reg.return_value.list_by_domain.return_value = caps

        stats = _make_exec_stats(total=0, success=0, failed=0)
        mock_engine.return_value.get_autonomy_status.return_value = _make_autonomy_status(
            can_run=False, source="risk_blocked", stats=stats
        )

        result = _render_domain_capabilities("service")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Extended coverage: _render_capability_detail
# ---------------------------------------------------------------------------


class TestRenderCapabilityDetail:
    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    def test_detail_with_side_effects(self, mock_engine):
        from elle.cli.capabilities_commands import _render_capability_detail

        effect = SimpleNamespace(kind="restart", target="nginx", reversible=True)
        spec = _make_cap_spec(
            name="service.restart",
            side_effects=(effect,),
            dependencies=("systemd",),
        )

        stats = _make_exec_stats(total=5, success=4, failed=1, last_at=datetime.utcnow())
        mock_engine.return_value.get_autonomy_status.return_value = _make_autonomy_status(
            can_run=True, source="earned", stats=stats, earned_progress=0.75
        )

        result = _render_capability_detail(spec)
        assert isinstance(result, str)

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    def test_detail_no_executions(self, mock_engine):
        from elle.cli.capabilities_commands import _render_capability_detail

        spec = _make_cap_spec(name="file.read")

        stats = _make_exec_stats(total=0, success=0, failed=0)
        mock_engine.return_value.get_autonomy_status.return_value = _make_autonomy_status(
            can_run=False, source="risk_blocked", stats=stats
        )

        result = _render_capability_detail(spec)
        assert "No executions recorded" in result

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    def test_detail_irreversible_side_effect(self, mock_engine):
        from elle.cli.capabilities_commands import _render_capability_detail

        effect = SimpleNamespace(kind="delete", target="/data", reversible=False)
        spec = _make_cap_spec(
            name="file.delete",
            side_effects=(effect,),
        )
        stats = _make_exec_stats(total=0, success=0, failed=0)
        mock_engine.return_value.get_autonomy_status.return_value = _make_autonomy_status(
            can_run=False, stats=stats
        )

        result = _render_capability_detail(spec)
        assert isinstance(result, str)

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    def test_detail_with_earned_progress_none(self, mock_engine):
        from elle.cli.capabilities_commands import _render_capability_detail

        spec = _make_cap_spec(name="file.read")
        stats = _make_exec_stats(total=0, success=0, failed=0)
        mock_engine.return_value.get_autonomy_status.return_value = _make_autonomy_status(
            can_run=False, stats=stats, earned_progress=None
        )

        result = _render_capability_detail(spec)
        assert isinstance(result, str)
        # Should not contain progress bar when earned_progress is None
        assert "[" not in result or "Earned progress" not in result


# ---------------------------------------------------------------------------
# Extended coverage: _render_autonomy_status
# ---------------------------------------------------------------------------


class TestRenderAutonomyStatus:
    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    def test_status_with_earned_enabled_and_overrides(self, mock_engine):
        from elle.cli.capabilities_commands import AutonomyLevel, _render_autonomy_status

        prefs = _make_prefs_enum(AutonomyLevel.LOW_RISK, earned_enabled=True)
        override = SimpleNamespace(
            capability_name="service.restart",
            autonomous=True,
            reason="manual",
        )
        mock_engine.return_value.store.get_preferences.return_value = prefs
        mock_engine.return_value.store.list_overrides.return_value = [override]

        result = _render_autonomy_status()
        assert isinstance(result, str)
        assert "Overrides" in result

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    def test_status_earned_disabled_no_overrides(self, mock_engine):
        from elle.cli.capabilities_commands import AutonomyLevel, _render_autonomy_status

        prefs = _make_prefs_enum(AutonomyLevel.LOW_RISK, earned_enabled=False)
        mock_engine.return_value.store.get_preferences.return_value = prefs
        mock_engine.return_value.store.list_overrides.return_value = []

        result = _render_autonomy_status()
        assert isinstance(result, str)
        assert "disabled" in result

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    def test_status_with_revoke_overrides(self, mock_engine):
        from elle.cli.capabilities_commands import AutonomyLevel, _render_autonomy_status

        prefs = _make_prefs_enum(AutonomyLevel.LOW_RISK)
        override_grant = SimpleNamespace(
            capability_name="service.restart",
            autonomous=True,
            reason="manual",
        )
        override_revoke = SimpleNamespace(
            capability_name="file.delete",
            autonomous=False,
            reason="too risky",
        )
        mock_engine.return_value.store.get_preferences.return_value = prefs
        mock_engine.return_value.store.list_overrides.return_value = [
            override_grant,
            override_revoke,
        ]

        result = _render_autonomy_status()
        assert "2" in result  # 2 overrides
        assert "1 grants" in result
        assert "1 revokes" in result


# ---------------------------------------------------------------------------
# Extended: _render_overrides with revoked override
# ---------------------------------------------------------------------------


class TestRenderOverridesExtended:
    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    def test_with_revoked_override(self, mock_engine):
        from elle.cli.capabilities_commands import _render_overrides

        override = SimpleNamespace(
            capability_name="file.delete",
            autonomous=False,
            reason="too dangerous",
        )
        mock_engine.return_value.store.list_overrides.return_value = [override]
        result = _render_overrides()
        assert "file.delete" in result
        assert "revoked" in result


# ---------------------------------------------------------------------------
# Extended: handle_capabilities_command domain prefix match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHandleCapabilitiesCommandExtended:
    @patch("elle.cli.capabilities_commands.get_registry")
    @patch("elle.cli.capabilities_commands._render_domain_capabilities", return_value="DOMAIN_BY_PREFIX")
    async def test_partial_name_domain_prefix(self, mock_render, mock_get_reg):
        """When arg has a dot but full name not found, should match domain prefix."""
        from elle.cli.capabilities_commands import handle_capabilities_command

        mock_get_reg.return_value.list_domains.return_value = ["service"]
        mock_get_reg.return_value.get.return_value = None  # full name not found
        result = await handle_capabilities_command("/capabilities service.nonexist", _make_session())
        assert result.output == "DOMAIN_BY_PREFIX"


# ---------------------------------------------------------------------------
# Extended: handle_autonomy_command earn synonyms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHandleAutonomyCommandExtended:
    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_earn_enable_synonym(self, mock_engine):
        from elle.cli.capabilities_commands import handle_autonomy_command

        result = await handle_autonomy_command("/autonomy earn enable", _make_session())
        assert "enabled" in result.output

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_earn_true_synonym(self, mock_engine):
        from elle.cli.capabilities_commands import handle_autonomy_command

        result = await handle_autonomy_command("/autonomy earn true", _make_session())
        assert "enabled" in result.output

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_earn_1_synonym(self, mock_engine):
        from elle.cli.capabilities_commands import handle_autonomy_command

        result = await handle_autonomy_command("/autonomy earn 1", _make_session())
        assert "enabled" in result.output

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_earn_disable_synonym(self, mock_engine):
        from elle.cli.capabilities_commands import handle_autonomy_command

        result = await handle_autonomy_command("/autonomy earn disable", _make_session())
        assert "disabled" in result.output

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_earn_false_synonym(self, mock_engine):
        from elle.cli.capabilities_commands import handle_autonomy_command

        result = await handle_autonomy_command("/autonomy earn false", _make_session())
        assert "disabled" in result.output

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_earn_0_synonym(self, mock_engine):
        from elle.cli.capabilities_commands import handle_autonomy_command

        result = await handle_autonomy_command("/autonomy earn 0", _make_session())
        assert "disabled" in result.output

    @patch("elle.cli.capabilities_commands.get_registry")
    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_revoke_nonexistent_cap(self, mock_engine, mock_reg):
        from elle.cli.capabilities_commands import handle_autonomy_command

        mock_reg.return_value.get.return_value = None
        result = await handle_autonomy_command("/autonomy revoke nosuchcap", _make_session())
        assert "not found" in result.output

    @patch("elle.cli.capabilities_commands.get_autonomy_engine")
    async def test_set_with_hyphen_conversion(self, mock_engine):
        from elle.cli.capabilities_commands import handle_autonomy_command

        result = await handle_autonomy_command("/autonomy set low-risk", _make_session())
        assert "level set to" in result.output


# ---------------------------------------------------------------------------
# Extended: handle_capability_query NL routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHandleCapabilityQueryExtended:
    @patch("elle.cli.capabilities_commands._render_domain_summary", return_value="SUMMARY")
    async def test_tell_me_what_you_can_do(self, mock_render):
        from elle.cli.capabilities_commands import handle_capability_query

        result = await handle_capability_query("tell me what you can do", _make_session())
        assert result.output == "SUMMARY"

    @patch("elle.cli.capabilities_commands._render_domain_summary", return_value="SUMMARY")
    async def test_show_capabilities(self, mock_render):
        from elle.cli.capabilities_commands import handle_capability_query

        result = await handle_capability_query("show capabilities", _make_session())
        assert result.output == "SUMMARY"

    @patch("elle.cli.capabilities_commands._render_domain_summary", return_value="SUMMARY")
    async def test_list_capabilities(self, mock_render):
        from elle.cli.capabilities_commands import handle_capability_query

        result = await handle_capability_query("list capabilities", _make_session())
        assert result.output == "SUMMARY"

    @patch("elle.cli.capabilities_commands._render_autonomy_status", return_value="AUTO_STATUS")
    async def test_what_runs_autonomously(self, mock_render):
        from elle.cli.capabilities_commands import handle_capability_query

        result = await handle_capability_query("what runs autonomously", _make_session())
        assert result.output == "AUTO_STATUS"

    @patch("elle.cli.capabilities_commands._render_autonomy_status", return_value="AUTO_STATUS")
    async def test_autonomy_status_query(self, mock_render):
        from elle.cli.capabilities_commands import handle_capability_query

        result = await handle_capability_query("autonomy status", _make_session())
        assert result.output == "AUTO_STATUS"

    @patch("elle.cli.capabilities_commands.get_registry")
    @patch("elle.cli.capabilities_commands._render_domain_capabilities", return_value="FILE_CAPS")
    async def test_capabilities_for_domain(self, mock_render, mock_reg):
        from elle.cli.capabilities_commands import handle_capability_query

        mock_reg.return_value.list_domains.return_value = ["file"]
        result = await handle_capability_query("capabilities for file", _make_session())
        assert result.output == "FILE_CAPS"


# ---------------------------------------------------------------------------
# Extended: _level_description coverage
# ---------------------------------------------------------------------------


class TestLevelDescriptionExtended:
    def test_medium_risk(self):
        from elle.cli.capabilities_commands import AutonomyLevel, _level_description

        result = _level_description(AutonomyLevel.MEDIUM_RISK)
        assert "medium" in result

    def test_high_risk(self):
        from elle.cli.capabilities_commands import AutonomyLevel, _level_description

        result = _level_description(AutonomyLevel.HIGH_RISK)
        assert "high" in result

    def test_low_risk(self):
        from elle.cli.capabilities_commands import AutonomyLevel, _level_description

        result = _level_description(AutonomyLevel.LOW_RISK)
        assert "low" in result


# ---------------------------------------------------------------------------
# Extended: risk_bar mixed distributions
# ---------------------------------------------------------------------------


class TestRiskBarExtended:
    def test_single_critical(self):
        from elle.cli.capabilities_commands import risk_bar

        bar = risk_bar({"critical": 1})
        assert len(bar.plain) > 0

    def test_all_none_risk(self):
        from elle.cli.capabilities_commands import risk_bar

        bar = risk_bar({"none": 5})
        assert len(bar.plain) > 0

    def test_medium_only(self):
        from elle.cli.capabilities_commands import risk_bar

        bar = risk_bar({"medium": 3})
        assert len(bar.plain) > 0

    def test_custom_width(self):
        from elle.cli.capabilities_commands import risk_bar

        bar = risk_bar({"low": 5}, width=20)
        assert len(bar.plain) > 0


# ---------------------------------------------------------------------------
# Extended: _progress_bar edge cases
# ---------------------------------------------------------------------------


class TestProgressBarExtended:
    def test_custom_width(self):
        from elle.cli.capabilities_commands import _progress_bar

        result = _progress_bar(0.5, width=20)
        assert len(result) == 22  # brackets + 20 chars

    def test_tiny_progress(self):
        from elle.cli.capabilities_commands import _progress_bar

        result = _progress_bar(0.01)
        # Should have at least the bracket structure
        assert result.startswith("[")
        assert result.endswith("]")
