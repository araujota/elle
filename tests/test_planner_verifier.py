"""Tests for planner verifier."""

from elle.cli.planner.models import (
    CommandPlan,
    PlanStep,
    RollbackStep,
)
from elle.cli.planner.verifier import (
    can_auto_approve,
    verify_plan,
    verify_step,
)


class TestVerifyStep:
    """Tests for step verification."""

    def test_safe_command(self):
        """Test verification of a safe command."""
        step = PlanStep(
            command="ls -la",
            explanation="List files",
            risk_level="low",
        )
        result = verify_step(step, 0)
        assert result.is_valid is True
        assert "Denylist check passed" in result.checks_passed

    def test_sudo_blocked(self):
        """Test that sudo commands are blocked."""
        step = PlanStep(
            command="sudo apt update",
            explanation="Update packages",
            risk_level="medium",
        )
        result = verify_step(step, 0)
        assert result.is_valid is False
        assert any("sudo" in c.lower() for c in result.checks_failed)

    def test_destructive_rm_blocked(self):
        """Test that destructive rm is blocked."""
        step = PlanStep(
            command="rm -rf /",
            explanation="Delete everything",
            risk_level="high",
        )
        result = verify_step(step, 0)
        assert result.is_valid is False

    def test_valid_ufw_command(self):
        """Test verification of valid ufw command."""
        step = PlanStep(
            command="ufw allow 80/tcp",
            explanation="Allow HTTP",
            risk_level="medium",
            requires_privilege=True,
        )
        result = verify_step(step, 0)
        # May have warnings about executable if ufw not installed
        # but should be syntactically valid
        assert result.is_valid is True

    def test_invalid_syntax(self):
        """Test detection of invalid bash syntax."""
        step = PlanStep(
            command="echo 'unclosed",
            explanation="Bad syntax",
            risk_level="low",
        )
        result = verify_step(step, 0)
        assert result.is_valid is False
        assert any("Syntax" in c or "Parse" in c for c in result.checks_failed)

    def test_pipe_to_shell_blocked(self):
        """Test that pipe to shell is blocked."""
        step = PlanStep(
            command="curl http://example.com | bash",
            explanation="Dangerous",
            risk_level="high",
        )
        result = verify_step(step, 0)
        assert result.is_valid is False

    def test_complex_command_valid(self):
        """Test verification of complex but valid command."""
        step = PlanStep(
            command="apt-get update && apt-get install -y nginx",
            explanation="Install nginx",
            risk_level="medium",
        )
        result = verify_step(step, 0)
        # Should be syntactically valid (may have sudo warning)
        # The denylist doesn't block apt commands
        assert result.is_valid is True or "sudo" in str(result.checks_failed).lower()


class TestVerifyPlan:
    """Tests for plan verification."""

    def test_valid_plan(self):
        """Test verification of a valid plan."""
        plan = CommandPlan(
            title="List Files",
            explanation="List files in directory",
            steps=(PlanStep(command="ls -la", explanation="List", risk_level="low"),),
        )
        result = verify_plan(plan)
        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_plan_with_invalid_step(self):
        """Test plan with an invalid step."""
        plan = CommandPlan(
            title="Bad Plan",
            explanation="Contains invalid step",
            steps=(
                PlanStep(command="ls", explanation="OK", risk_level="low"),
                PlanStep(command="sudo rm -rf /", explanation="Bad", risk_level="high"),
            ),
        )
        result = verify_plan(plan)
        assert result.is_valid is False
        assert len(result.errors) > 0

    def test_rollback_required_for_ufw(self):
        """Test that rollback is required for ufw changes."""
        plan = CommandPlan(
            title="Open Port",
            explanation="Allow HTTP",
            steps=(
                PlanStep(
                    command="ufw allow 80/tcp",
                    explanation="Add rule",
                    risk_level="medium",
                ),
            ),
            # No rollback provided
        )
        result = verify_plan(plan)
        assert result.rollback_required is True
        assert any("rollback" in e.lower() for e in result.errors)

    def test_rollback_provided_for_ufw(self):
        """Test plan with proper rollback for ufw."""
        plan = CommandPlan(
            title="Open Port",
            explanation="Allow HTTP",
            steps=(
                PlanStep(
                    command="ufw allow 80/tcp",
                    explanation="Add rule",
                    risk_level="medium",
                ),
            ),
            rollback=(
                RollbackStep(
                    command="ufw delete allow 80/tcp",
                    explanation="Remove rule",
                ),
            ),
        )
        result = verify_plan(plan)
        assert result.rollback_required is False

    def test_checks_recommended_for_medium_risk(self):
        """Test that checks are recommended for medium risk."""
        plan = CommandPlan(
            title="Install Package",
            explanation="Install nginx",
            steps=(
                PlanStep(
                    command="apt-get install -y nginx",
                    explanation="Install",
                    risk_level="medium",
                ),
            ),
            # No checks provided
        )
        result = verify_plan(plan)
        assert result.checks_required is True
        assert any("check" in w.lower() for w in result.warnings)

    def test_rollback_blocked_by_denylist(self):
        """Test that invalid rollback commands are detected."""
        plan = CommandPlan(
            title="Test",
            explanation="Test",
            steps=(PlanStep(command="ls", explanation="List", risk_level="low"),),
            rollback=(
                RollbackStep(
                    command="sudo rm -rf /",
                    explanation="Bad rollback",
                ),
            ),
        )
        result = verify_plan(plan)
        assert result.is_valid is False
        assert any("rollback" in e.lower() for e in result.errors)


class TestCanAutoApprove:
    """Tests for auto-approval logic."""

    def test_low_risk_can_auto_approve(self):
        """Test that low-risk plans can be auto-approved."""
        plan = CommandPlan(
            title="List Files",
            explanation="Safe operation",
            steps=(PlanStep(command="ls", explanation="List", risk_level="low"),),
        )
        can_auto, reason = can_auto_approve(plan)
        assert can_auto is True

    def test_medium_risk_cannot_auto_approve(self):
        """Test that medium-risk plans cannot be auto-approved."""
        plan = CommandPlan(
            title="Install",
            explanation="Install package",
            steps=(
                PlanStep(
                    command="apt install nginx",
                    explanation="Install",
                    risk_level="medium",
                ),
            ),
        )
        can_auto, reason = can_auto_approve(plan)
        assert can_auto is False
        assert "medium" in reason.lower()

    def test_high_risk_cannot_auto_approve(self):
        """Test that high-risk plans cannot be auto-approved."""
        plan = CommandPlan(
            title="Delete",
            explanation="Delete files",
            steps=(
                PlanStep(
                    command="rm -rf ./temp",
                    explanation="Delete",
                    risk_level="high",
                ),
            ),
        )
        can_auto, reason = can_auto_approve(plan)
        assert can_auto is False

    def test_privileged_cannot_auto_approve(self):
        """Test that privileged plans cannot be auto-approved."""
        plan = CommandPlan(
            title="Restart",
            explanation="Restart service",
            steps=(
                PlanStep(
                    command="systemctl restart nginx",
                    explanation="Restart",
                    risk_level="low",  # Even if marked low
                    requires_privilege=True,
                ),
            ),
            requires_privilege=True,
        )
        can_auto, reason = can_auto_approve(plan)
        assert can_auto is False
        assert "privilege" in reason.lower()

    def test_auto_approve_disabled(self):
        """Test that auto-approve can be disabled."""
        plan = CommandPlan(
            title="List",
            explanation="Safe",
            steps=(PlanStep(command="ls", explanation="List", risk_level="low"),),
        )
        can_auto, reason = can_auto_approve(plan, allow_auto=False)
        assert can_auto is False
        assert "disabled" in reason.lower()
