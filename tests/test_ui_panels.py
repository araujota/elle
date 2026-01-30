from __future__ import annotations

"""Tests for elle.cli.ui.panels -- Rich panel rendering code."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from elle.cli.ui.panels import (
    apply_confirmation,
    config_diff_panel,
    fixit_compact,
    fixit_panel,
    incident_compact,
    incident_panel,
    plan_panel,
    plan_step_result,
    rationale_panel,
    rollback_confirmation,
    search_results_panel,
)


# ---------------------------------------------------------------------------
# fixit_panel
# ---------------------------------------------------------------------------

class TestFixitPanel:
    """Tests for the fixit_panel function."""

    def test_minimal_fixit_panel(self):
        result = fixit_panel(
            command="apt install foo",
            exit_code=1,
            diagnosis="Package not found.",
        )
        assert isinstance(result, Panel)

    def test_fixit_panel_with_root_cause(self):
        result = fixit_panel(
            command="apt install foo",
            exit_code=1,
            diagnosis="Package not found.",
            root_cause="The package name is misspelled.",
        )
        assert isinstance(result, Panel)

    def test_fixit_panel_with_confidence(self):
        result = fixit_panel(
            command="systemctl restart nginx",
            exit_code=1,
            diagnosis="Service not running.",
            confidence=0.85,
        )
        assert isinstance(result, Panel)

    def test_fixit_panel_with_confidence_breakdown_overrides_legacy(self):
        """confidence_breakdown should be used instead of legacy confidence."""
        result = fixit_panel(
            command="systemctl restart nginx",
            exit_code=1,
            diagnosis="Service not running.",
            confidence=0.5,
            confidence_breakdown={"overall": 0.9, "from_man_vault": 0.3, "from_incident_vault": 0.4, "from_llm": 0.2},
        )
        assert isinstance(result, Panel)

    def test_fixit_panel_with_suggestions(self):
        result = fixit_panel(
            command="apt install foo",
            exit_code=1,
            diagnosis="Package not found.",
            suggestions=[
                {"command": "apt install foobar", "explanation": "Corrected package name", "risk": "low"},
                {"command": "apt search foo", "explanation": "Search for the package", "risk": "low"},
            ],
        )
        assert isinstance(result, Panel)

    def test_fixit_panel_suggestion_defaults(self):
        """Suggestion dicts may be sparse."""
        result = fixit_panel(
            command="cmd",
            exit_code=2,
            diagnosis="diag",
            suggestions=[{}],
        )
        assert isinstance(result, Panel)

    def test_fixit_panel_with_verification_commands(self):
        result = fixit_panel(
            command="ufw allow 80",
            exit_code=1,
            diagnosis="Port already in use.",
            verification_commands=["ufw status", "ss -tlnp"],
        )
        assert isinstance(result, Panel)

    def test_fixit_panel_with_learning_resources_no_provenance(self):
        result = fixit_panel(
            command="cmd",
            exit_code=1,
            diagnosis="diag",
            learning_resources=["man systemctl", "man journalctl"],
        )
        assert isinstance(result, Panel)

    def test_fixit_panel_learning_resources_hidden_when_provenance(self):
        """Learning resources should not appear when provenance is present."""
        result = fixit_panel(
            command="cmd",
            exit_code=1,
            diagnosis="diag",
            learning_resources=["man foo"],
            man_citations=[{"name": "foo", "section": "1", "snippet": "desc", "relevance_score": 0.8}],
        )
        assert isinstance(result, Panel)

    def test_fixit_panel_with_privilege_and_incident_id(self):
        result = fixit_panel(
            command="cmd",
            exit_code=1,
            diagnosis="diag",
            requires_privilege=True,
            incident_id="inc-123-abc",
        )
        assert isinstance(result, Panel)

    def test_fixit_panel_rationale_hidden_when_show_rationale_false(self):
        result = fixit_panel(
            command="cmd",
            exit_code=1,
            diagnosis="diag",
            man_citations=[{"name": "foo", "section": "1", "snippet": "s", "relevance_score": 0.5}],
            show_rationale=False,
        )
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# fixit_compact
# ---------------------------------------------------------------------------

class TestFixitCompact:
    def test_returns_text(self):
        result = fixit_compact(command="apt install foo", fix_command="apt install foobar")
        assert isinstance(result, Text)

    def test_risk_levels(self):
        for risk in ("low", "medium", "high"):
            result = fixit_compact(command="cmd", fix_command="fix", risk=risk)
            assert isinstance(result, Text)


# ---------------------------------------------------------------------------
# rationale_panel
# ---------------------------------------------------------------------------

class TestRationalePanel:
    def test_empty_rationale(self):
        result = rationale_panel()
        assert isinstance(result, Panel)

    def test_with_summary(self):
        result = rationale_panel(summary="Suggested because man systemctl(1)")
        assert isinstance(result, Panel)

    def test_with_man_citations(self):
        citations = [
            {"name": "systemctl", "section": "1", "snippet": "systemctl restart", "relevance_score": 0.9},
            {"name": "journalctl", "section": "1", "snippet": "", "relevance_score": 0.5},
        ]
        result = rationale_panel(man_citations=citations)
        assert isinstance(result, Panel)

    def test_with_long_snippet_truncated(self):
        citations = [{"name": "cmd", "section": "1", "snippet": "x" * 200, "relevance_score": 0.7}]
        result = rationale_panel(man_citations=citations)
        assert isinstance(result, Panel)

    def test_with_incident_citations(self):
        citations = [
            {
                "title": "DNS resolution failure on eth0 interface",
                "outcome": "improved",
                "similarity_score": 0.85,
                "successful_actions": ["systemctl restart systemd-resolved"],
            },
        ]
        result = rationale_panel(incident_citations=citations)
        assert isinstance(result, Panel)

    def test_incident_citation_long_title(self):
        citations = [{"title": "A" * 60, "outcome": "partial", "similarity_score": 0.6}]
        result = rationale_panel(incident_citations=citations)
        assert isinstance(result, Panel)

    def test_with_confidence_breakdown(self):
        confidence = {"overall": 0.8, "from_man_vault": 0.3, "from_incident_vault": 0.2, "from_llm": 0.3}
        result = rationale_panel(confidence=confidence)
        assert isinstance(result, Panel)

    def test_confidence_with_zeros(self):
        confidence = {"overall": 0.5, "from_man_vault": 0.0, "from_incident_vault": 0.0, "from_llm": 0.5}
        result = rationale_panel(confidence=confidence)
        assert isinstance(result, Panel)

    def test_full_rationale_panel(self):
        result = rationale_panel(
            summary="Based on man systemctl(1) and 1 similar incident",
            man_citations=[{"name": "systemctl", "section": "1", "snippet": "some text", "relevance_score": 0.8}],
            incident_citations=[{"title": "title", "outcome": "improved", "similarity_score": 0.7}],
            confidence={"overall": 0.8, "from_man_vault": 0.3, "from_incident_vault": 0.2, "from_llm": 0.3},
        )
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# plan_panel
# ---------------------------------------------------------------------------

class TestPlanPanel:
    def test_basic_plan_panel(self):
        result = plan_panel(
            title="Restart Nginx",
            explanation="Restart the nginx service.",
            steps=[{"description": "Restart nginx", "command": "systemctl restart nginx"}],
        )
        assert isinstance(result, Panel)

    def test_plan_panel_with_risks(self):
        result = plan_panel(
            title="Plan",
            explanation="Explanation",
            steps=[{"description": "Step 1"}],
            risks=["May interrupt connections"],
        )
        assert isinstance(result, Panel)

    def test_plan_panel_with_rollback_steps(self):
        result = plan_panel(
            title="Plan",
            explanation="Explanation",
            steps=[{"description": "Step 1"}],
            rollback_steps=[{"description": "Undo step 1"}],
        )
        assert isinstance(result, Panel)

    def test_plan_panel_current_step_before(self):
        steps = [
            {"description": "Done step"},
            {"description": "Current step"},
            {"description": "Pending step"},
        ]
        result = plan_panel(title="P", explanation="E", steps=steps, current_step=1)
        assert isinstance(result, Panel)

    def test_plan_panel_no_rollback_flag(self):
        steps = [{"description": "Step", "can_rollback": False}]
        result = plan_panel(title="P", explanation="E", steps=steps)
        assert isinstance(result, Panel)

    def test_plan_panel_with_verification(self):
        steps = [{"description": "Step", "command": "cmd", "verification": "verify cmd"}]
        result = plan_panel(title="P", explanation="E", steps=steps)
        assert isinstance(result, Panel)

    def test_plan_panel_requires_privilege(self):
        result = plan_panel(
            title="P",
            explanation="E",
            steps=[{"description": "Step"}],
            requires_privilege=True,
        )
        assert isinstance(result, Panel)

    def test_plan_panel_with_rationale(self):
        result = plan_panel(
            title="P",
            explanation="E",
            steps=[{"description": "Step"}],
            confidence={"overall": 0.8, "from_man_vault": 0.3, "from_incident_vault": 0.2, "from_llm": 0.3},
            rationale_summary="Because docs",
        )
        assert isinstance(result, Panel)

    def test_plan_panel_rationale_hidden(self):
        result = plan_panel(
            title="P",
            explanation="E",
            steps=[{"description": "Step"}],
            confidence={"overall": 0.5},
            show_rationale=False,
        )
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# plan_step_result
# ---------------------------------------------------------------------------

class TestPlanStepResult:
    def test_success(self):
        result = plan_step_result(1, "Install package", success=True)
        assert isinstance(result, Text)

    def test_failure(self):
        result = plan_step_result(2, "Restart service", success=False, error="Permission denied")
        assert isinstance(result, Text)

    def test_success_with_output(self):
        result = plan_step_result(1, "Check status", success=True, output="active")
        assert isinstance(result, Text)


# ---------------------------------------------------------------------------
# incident_panel
# ---------------------------------------------------------------------------

class TestIncidentPanel:
    def test_minimal_incident_panel(self):
        result = incident_panel(
            incident_id="inc-abc-123",
            title="DNS failure",
            domain="net",
            status="open",
            severity="warning",
            created_at=datetime.now() - timedelta(minutes=5),
        )
        assert isinstance(result, Panel)

    def test_incident_panel_all_fields(self):
        result = incident_panel(
            incident_id="inc-abc-123",
            title="Disk full",
            domain="disk",
            status="resolved",
            severity="critical",
            created_at=datetime.now() - timedelta(hours=2),
            summary="Root partition is at 99%",
            symptoms=["No space left on device", "Journal write failures"],
            root_cause="Log rotation not configured",
            outcome="improved",
            actions_count=3,
            similar_incidents=2,
        )
        assert isinstance(result, Panel)

    def test_incident_panel_false_positive(self):
        result = incident_panel(
            incident_id="id",
            title="title",
            domain="auth",
            status="false_positive",
            severity="info",
            created_at=datetime.now(),
        )
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# incident_compact
# ---------------------------------------------------------------------------

class TestIncidentCompact:
    def test_returns_text(self):
        result = incident_compact(
            incident_id="inc-abc-12345",
            title="Network issue",
            domain="net",
            status="open",
            created_at=datetime.now() - timedelta(seconds=30),
        )
        assert isinstance(result, Text)


# ---------------------------------------------------------------------------
# search_results_panel
# ---------------------------------------------------------------------------

class TestSearchResultsPanel:
    def test_empty_results(self):
        result = search_results_panel("nginx", [])
        assert isinstance(result, Panel)

    def test_with_results(self):
        results = [
            {"title": "nginx(1)", "snippet": "nginx is a web server", "relevance": 0.95},
            {"title": "nginx.conf(5)", "snippet": "config file for nginx", "relevance": 0.8},
        ]
        result = search_results_panel("nginx", results)
        assert isinstance(result, Panel)

    def test_long_snippet_truncated(self):
        results = [{"title": "T", "snippet": "x" * 200, "relevance": 0.5}]
        result = search_results_panel("q", results)
        assert isinstance(result, Panel)

    def test_custom_source(self):
        result = search_results_panel("query", [], source="Incident Vault")
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# config_diff_panel
# ---------------------------------------------------------------------------

class TestConfigDiffPanel:
    def test_basic_config_diff(self):
        diff = "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new"
        result = config_diff_panel("/etc/ssh/sshd_config", diff)
        assert isinstance(result, Panel)

    def test_with_operations(self):
        diff = "--- a\n+++ b\n"
        ops = [
            {"kind": "set", "path": "/PermitRootLogin", "value": "no"},
            {"kind": "rm", "path": "/PasswordAuthentication", "value": ""},
            {"kind": "other", "path": "/SomeKey", "value": "val"},
        ]
        result = config_diff_panel("/etc/ssh/sshd_config", diff, operations=ops)
        assert isinstance(result, Panel)

    def test_with_risks_and_validation(self):
        diff = "--- a\n+++ b\n"
        result = config_diff_panel(
            "/etc/ssh/sshd_config",
            diff,
            risks=["May lock you out of SSH"],
            validation_command="sshd -t",
            requires_privilege=True,
        )
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# apply_confirmation / rollback_confirmation
# ---------------------------------------------------------------------------

class TestConfirmationPanels:
    def test_apply_confirmation_low_risk(self):
        result = apply_confirmation(action="Restart nginx", risk="low")
        assert isinstance(result, Text)

    def test_apply_confirmation_high_risk_with_privilege(self):
        result = apply_confirmation(action="Modify sshd_config", risk="high", requires_privilege=True)
        assert isinstance(result, Text)

    def test_rollback_confirmation(self):
        result = rollback_confirmation(reason="Step 2 failed unexpectedly")
        assert isinstance(result, Text)
