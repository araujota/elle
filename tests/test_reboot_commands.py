"""Tests for cli/reboot/commands.py - reboot persistence and recovery commands."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _make_session():
    """Return a minimal real session."""
    from elle.common.session import create_session

    return create_session()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestLooksLikeUUID:
    def test_full_uuid(self):
        from elle.cli.reboot.commands import _looks_like_uuid

        assert _looks_like_uuid("abcdef12-3456-7890-abcd-ef1234567890") is True

    def test_partial_uuid(self):
        from elle.cli.reboot.commands import _looks_like_uuid

        assert _looks_like_uuid("abcdef12") is True

    def test_too_short(self):
        from elle.cli.reboot.commands import _looks_like_uuid

        assert _looks_like_uuid("abc") is False

    def test_invalid_chars(self):
        from elle.cli.reboot.commands import _looks_like_uuid

        assert _looks_like_uuid("gggggggg") is False

    def test_uppercase_normalized(self):
        from elle.cli.reboot.commands import _looks_like_uuid

        assert _looks_like_uuid("ABCDEF12") is True


class TestStatusColor:
    def test_known_statuses(self):
        from elle.cli.reboot.commands import _status_color
        from elle.cli.terminal.renderer import Colors

        assert _status_color("pending") == Colors.YELLOW
        assert _status_color("completed") == Colors.GREEN
        assert _status_color("failed") == Colors.RED

    def test_unknown_status(self):
        from elle.cli.reboot.commands import _status_color
        from elle.cli.terminal.renderer import Colors

        assert _status_color("unknown_status") == Colors.RESET


class TestOutcomeStr:
    def test_improved(self):
        from elle.cli.reboot.commands import _outcome_str

        result = _outcome_str("improved")
        assert "improved" in result

    def test_failed(self):
        from elle.cli.reboot.commands import _outcome_str

        result = _outcome_str("failed")
        assert "failed" in result

    def test_unknown_outcome(self):
        from elle.cli.reboot.commands import _outcome_str

        result = _outcome_str("custom")
        assert result == "custom"


class TestFormatDatetime:
    def test_none(self):
        from elle.cli.reboot.commands import _format_datetime

        assert _format_datetime(None) == "N/A"

    def test_valid_datetime(self):
        from elle.cli.reboot.commands import _format_datetime

        dt = datetime(2024, 3, 15, 14, 30)
        result = _format_datetime(dt)
        assert "2024-03-15" in result
        assert "14:30" in result


# ---------------------------------------------------------------------------
# _reboot_help
# ---------------------------------------------------------------------------


class TestRebootHelp:
    def test_contains_commands(self):
        from elle.cli.reboot.commands import _reboot_help

        help_text = _reboot_help()
        assert "reboot status" in help_text
        assert "reboot history" in help_text
        assert "reboot cancel" in help_text
        assert "reboot confirm" in help_text
        assert "reboot rollback" in help_text


# ---------------------------------------------------------------------------
# handle_reboot_command - routing
# ---------------------------------------------------------------------------


class TestHandleRebootCommand:
    @patch("elle.cli.reboot.commands.render_reboot_status", return_value="STATUS")
    def test_default_subcommand_is_status(self, mock_status):
        from elle.cli.reboot.commands import handle_reboot_command

        output, success = handle_reboot_command("reboot", _make_session())
        assert output == "STATUS"
        assert success is True

    @patch("elle.cli.reboot.commands.render_reboot_status", return_value="STATUS")
    def test_explicit_status(self, mock_status):
        from elle.cli.reboot.commands import handle_reboot_command

        output, success = handle_reboot_command("reboot status", _make_session())
        assert output == "STATUS"

    @patch("elle.cli.reboot.commands.render_reboot_history", return_value="HISTORY")
    def test_history_default_limit(self, mock_history):
        from elle.cli.reboot.commands import handle_reboot_command

        output, success = handle_reboot_command("reboot history", _make_session())
        assert output == "HISTORY"
        mock_history.assert_called_once_with(20)

    @patch("elle.cli.reboot.commands.render_reboot_history", return_value="HISTORY")
    def test_history_custom_limit(self, mock_history):
        from elle.cli.reboot.commands import handle_reboot_command

        output, success = handle_reboot_command("reboot history 5", _make_session())
        mock_history.assert_called_once_with(5)

    @patch("elle.cli.reboot.commands.handle_cancel", return_value="CANCELLED")
    def test_cancel(self, mock_cancel):
        from elle.cli.reboot.commands import handle_reboot_command

        output, success = handle_reboot_command("reboot cancel", _make_session())
        assert output == "CANCELLED"

    @patch("elle.cli.reboot.commands.handle_confirm", return_value="CONFIRMED")
    def test_confirm(self, mock_confirm):
        from elle.cli.reboot.commands import handle_reboot_command

        output, success = handle_reboot_command("reboot confirm", _make_session())
        assert output == "CONFIRMED"

    @patch("elle.cli.reboot.commands.handle_rollback", return_value="ROLLBACK")
    def test_rollback(self, mock_rollback):
        from elle.cli.reboot.commands import handle_reboot_command

        output, success = handle_reboot_command("reboot rollback", _make_session())
        assert output == "ROLLBACK"

    @patch("elle.cli.reboot.commands.render_intent_detail", return_value="DETAIL")
    def test_uuid_subcommand(self, mock_detail):
        from elle.cli.reboot.commands import handle_reboot_command

        output, success = handle_reboot_command("reboot abcdef12-3456-7890", _make_session())
        assert output == "DETAIL"

    def test_unknown_subcommand_shows_help(self):
        from elle.cli.reboot.commands import handle_reboot_command

        output, success = handle_reboot_command("reboot xyzzy", _make_session())
        assert "Reboot Recovery Commands" in output


# ---------------------------------------------------------------------------
# render_reboot_status
# ---------------------------------------------------------------------------


class TestRenderRebootStatus:
    def test_no_active_intent(self):
        from elle.cli.reboot.commands import render_reboot_status

        mock_status = SimpleNamespace(
            total_intents=5,
            completed_count=3,
            failed_count=1,
            rolled_back_count=1,
            recent_intents=[],
        )
        mock_reboot = MagicMock()
        mock_reboot.get_reboot_status.return_value = mock_status
        mock_reboot.get_active_intent.return_value = None
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_reboot_status()
            assert "No active reboot" in result
            assert "5" in result

    def test_active_intent_verifying(self):
        from elle.cli.reboot.commands import render_reboot_status

        active = SimpleNamespace(
            id="abc12345-6789-0000-0000-000000000000",
            goal="Kernel update",
            status="verifying",
            reason="Kernel upgrade",
            created_at=datetime(2024, 3, 15, 10, 0),
            verification_attempts=1,
        )
        mock_status = SimpleNamespace(
            total_intents=1,
            completed_count=0,
            failed_count=0,
            rolled_back_count=0,
            recent_intents=[],
        )
        mock_reboot = MagicMock()
        mock_reboot.get_reboot_status.return_value = mock_status
        mock_reboot.get_active_intent.return_value = active
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_reboot_status()
            assert "Verification in progress" in result
            assert "1/3" in result

    def test_active_intent_failed(self):
        from elle.cli.reboot.commands import render_reboot_status

        active = SimpleNamespace(
            id="abc12345-6789-0000-0000-000000000000",
            goal="Kernel update",
            status="failed",
            reason="Kernel upgrade",
            created_at=datetime(2024, 3, 15, 10, 0),
            verification_attempts=3,
        )
        mock_status = SimpleNamespace(
            total_intents=1,
            completed_count=0,
            failed_count=1,
            rolled_back_count=0,
            recent_intents=[],
        )
        mock_reboot = MagicMock()
        mock_reboot.get_reboot_status.return_value = mock_status
        mock_reboot.get_active_intent.return_value = active
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_reboot_status()
            assert "Verification failed" in result

    def test_exception_handling(self):
        from elle.cli.reboot.commands import render_reboot_status

        mock_reboot = MagicMock()
        mock_reboot.get_reboot_status.side_effect = RuntimeError("db error")
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_reboot_status()
            assert "Error" in result

    def test_with_recent_intents(self):
        from elle.cli.reboot.commands import render_reboot_status

        recent = SimpleNamespace(
            status="completed",
            outcome="improved",
            goal="Apply security patches",
        )
        mock_status = SimpleNamespace(
            total_intents=2,
            completed_count=2,
            failed_count=0,
            rolled_back_count=0,
            recent_intents=[recent],
        )
        mock_reboot = MagicMock()
        mock_reboot.get_reboot_status.return_value = mock_status
        mock_reboot.get_active_intent.return_value = None
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_reboot_status()
            assert "Recent" in result


# ---------------------------------------------------------------------------
# render_reboot_history
# ---------------------------------------------------------------------------


class TestRenderRebootHistory:
    def test_no_history(self):
        from elle.cli.reboot.commands import render_reboot_history

        mock_reboot = MagicMock()
        mock_reboot.list_intents.return_value = []
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_reboot_history()
            assert "No reboot history" in result

    def test_with_history(self):
        from elle.cli.reboot.commands import render_reboot_history

        intent = SimpleNamespace(
            id="abc12345-6789-0000-0000-000000000000",
            status="completed",
            outcome="improved",
            goal="Kernel update",
            reason="Security patch",
            created_at=datetime(2024, 3, 15, 10, 0),
            verification_attempts=1,
            outcome_detail="All checks passed",
        )
        mock_reboot = MagicMock()
        mock_reboot.list_intents.return_value = [intent]
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_reboot_history()
            assert "Reboot History" in result
            assert "abc12345" in result
            assert "Kernel update" in result

    def test_exception_handling(self):
        from elle.cli.reboot.commands import render_reboot_history

        mock_reboot = MagicMock()
        mock_reboot.list_intents.side_effect = RuntimeError("db error")
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_reboot_history()
            assert "Error" in result


# ---------------------------------------------------------------------------
# render_intent_detail
# ---------------------------------------------------------------------------


class TestRenderIntentDetail:
    def test_not_found(self):
        from elle.cli.reboot.commands import render_intent_detail

        mock_reboot = MagicMock()
        mock_reboot.get_intent.return_value = None
        mock_reboot.list_intents.return_value = []
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_intent_detail("nonexistent")
            assert "not found" in result

    def test_multiple_matches(self):
        from elle.cli.reboot.commands import render_intent_detail

        i1 = SimpleNamespace(id="abc12345-aaaa", goal="Goal 1")
        i2 = SimpleNamespace(id="abc12345-bbbb", goal="Goal 2")
        mock_reboot = MagicMock()
        mock_reboot.get_intent.return_value = None
        mock_reboot.list_intents.return_value = [i1, i2]
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_intent_detail("abc12345")
            assert "Multiple intents" in result

    def test_found_exact(self):
        from elle.cli.reboot.commands import render_intent_detail

        intent = SimpleNamespace(
            id="abc12345-6789-0000-0000-000000000000",
            goal="Kernel update",
            status="completed",
            outcome="improved",
            reason="Security patch",
            created_at=datetime(2024, 3, 15, 10, 0),
            updated_at=datetime(2024, 3, 15, 10, 5),
            task_description="Apply kernel security patches",
            reason_detail="CVE-2024-1234 fix",
            boot_id="boot-old-123",
            new_boot_id="boot-new-456",
            grub_entry="Ubuntu, with Linux 6.5.0-45",
            grub_default_saved="0",
            verification_attempts=1,
            last_verification_at=datetime(2024, 3, 15, 10, 3),
            verifications=[
                SimpleNamespace(
                    passed=True, check_type="service", check_command="systemctl is-active nginx", required=True
                ),
                SimpleNamespace(passed=None, check_type="custom", check_command="echo ok", required=False),
            ],
            outcome_detail="All checks passed",
            incident_id="inc-001-abcdef",
        )
        mock_reboot = MagicMock()
        mock_reboot.get_intent.return_value = intent
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_intent_detail("abc12345-6789-0000-0000-000000000000")
            assert "Reboot Intent Detail" in result
            assert "Kernel update" in result
            assert "boot-old" in result
            assert "GRUB" in result
            assert "PASS" in result
            assert "inc-001" in result

    def test_found_with_failed_verification(self):
        from elle.cli.reboot.commands import render_intent_detail

        intent = SimpleNamespace(
            id="def12345-6789",
            goal="Update",
            status="failed",
            outcome="failed",
            reason="Update",
            created_at=datetime(2024, 3, 15, 10, 0),
            updated_at=datetime(2024, 3, 15, 10, 5),
            task_description=None,
            reason_detail=None,
            boot_id=None,
            new_boot_id=None,
            grub_entry=None,
            grub_default_saved=None,
            verification_attempts=3,
            last_verification_at=None,
            verifications=[
                SimpleNamespace(
                    passed=False, check_type="service", check_command="systemctl is-active nginx", required=True
                ),
            ],
            outcome_detail=None,
            incident_id=None,
        )
        mock_reboot = MagicMock()
        mock_reboot.get_intent.return_value = intent
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_intent_detail("def12345-6789")
            assert "FAIL" in result

    def test_exception_handling(self):
        from elle.cli.reboot.commands import render_intent_detail

        mock_reboot = MagicMock()
        mock_reboot.get_intent.side_effect = RuntimeError("db error")
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_intent_detail("abc123")
            assert "Error" in result


# ---------------------------------------------------------------------------
# handle_cancel
# ---------------------------------------------------------------------------


class TestHandleCancel:
    def test_no_active_intent(self):
        from elle.cli.reboot.commands import handle_cancel

        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.return_value = None
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = handle_cancel()
            assert "No active reboot" in result

    def test_cancel_failed_intent(self):
        from elle.cli.reboot.commands import handle_cancel

        active = SimpleNamespace(id="abc123", status="failed", goal="Kernel update")
        mock_manager = MagicMock()
        mock_manager.cancel_pending_rollback.return_value = True

        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.return_value = active
        mock_reboot.get_manager.return_value = mock_manager

        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            with patch("elle.cli.reboot.commands.asyncio") as mock_asyncio:
                mock_asyncio.run.return_value = True
                result = handle_cancel()
                assert "rollback cancelled" in result

    def test_cancel_pending_intent(self):
        from elle.cli.reboot.commands import handle_cancel

        active = SimpleNamespace(id="abc123", status="pending", goal="Kernel update")
        mock_manager = MagicMock()

        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.return_value = active
        mock_reboot.get_manager.return_value = mock_manager

        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            with patch("elle.cli.reboot.commands.asyncio") as mock_asyncio:
                mock_asyncio.run.return_value = True
                result = handle_cancel()
                assert "cancelled" in result.lower()

    def test_cancel_uncancellable_status(self):
        from elle.cli.reboot.commands import handle_cancel

        active = SimpleNamespace(id="abc123", status="completed", goal="Done")
        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.return_value = active
        mock_reboot.get_manager.return_value = MagicMock()
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            with patch("elle.cli.reboot.commands.asyncio") as mock_asyncio:
                mock_asyncio.run.return_value = False
                result = handle_cancel()
                assert "Cannot cancel" in result

    def test_cancel_exception(self):
        from elle.cli.reboot.commands import handle_cancel

        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.side_effect = RuntimeError("db error")
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = handle_cancel()
            assert "Error" in result


# ---------------------------------------------------------------------------
# handle_confirm
# ---------------------------------------------------------------------------


class TestHandleConfirm:
    def test_no_active_intent(self):
        from elle.cli.reboot.commands import handle_confirm

        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.return_value = None
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = handle_confirm()
            assert "No active reboot" in result

    def test_wrong_status(self):
        from elle.cli.reboot.commands import handle_confirm

        active = SimpleNamespace(id="abc123", status="pending", goal="Test")
        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.return_value = active
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = handle_confirm()
            assert "Cannot confirm" in result

    def test_confirm_success(self):
        from elle.cli.reboot.commands import handle_confirm

        active = SimpleNamespace(id="abc123", status="failed", goal="Test")
        confirm_result = SimpleNamespace(status="completed")
        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.return_value = active
        mock_reboot.get_manager.return_value = MagicMock()
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            with patch("elle.cli.reboot.commands.asyncio") as mock_asyncio:
                mock_asyncio.run.return_value = confirm_result
                result = handle_confirm()
                assert "confirmed" in result.lower()

    def test_confirm_failure(self):
        from elle.cli.reboot.commands import handle_confirm

        active = SimpleNamespace(id="abc123", status="verifying", goal="Test")
        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.return_value = active
        mock_reboot.get_manager.return_value = MagicMock()
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            with patch("elle.cli.reboot.commands.asyncio") as mock_asyncio:
                mock_asyncio.run.return_value = None
                result = handle_confirm()
                assert "Failed" in result

    def test_confirm_exception(self):
        from elle.cli.reboot.commands import handle_confirm

        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.side_effect = RuntimeError("db error")
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = handle_confirm()
            assert "Error" in result


# ---------------------------------------------------------------------------
# handle_rollback
# ---------------------------------------------------------------------------


class TestHandleRollback:
    def test_no_active_intent(self):
        from elle.cli.reboot.commands import handle_rollback

        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.return_value = None
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = handle_rollback()
            assert "No active reboot" in result

    def test_active_intent_shows_warning(self):
        from elle.cli.reboot.commands import handle_rollback

        active = SimpleNamespace(
            id="abc123",
            status="failed",
            goal="Kernel update",
            grub_default_saved="0",
        )
        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.return_value = active
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = handle_rollback()
            assert "WARNING" in result
            assert "confirm" in result.lower()

    def test_rollback_exception(self):
        from elle.cli.reboot.commands import handle_rollback

        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.side_effect = RuntimeError("db error")
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = handle_rollback()
            assert "Error" in result


# ---------------------------------------------------------------------------
# render_recent_reboots_summary
# ---------------------------------------------------------------------------


class TestRenderRecentRebootsSummary:
    def test_no_recent_returns_none(self):
        from elle.cli.reboot.commands import render_recent_reboots_summary

        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.return_value = None
        mock_reboot.list_recent_intents.return_value = []
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_recent_reboots_summary()
            assert result is None

    def test_with_active_intent(self):
        from elle.cli.reboot.commands import render_recent_reboots_summary

        active = SimpleNamespace(
            id="abc123",
            status="verifying",
            goal="Kernel update",
        )
        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.return_value = active
        mock_reboot.list_recent_intents.return_value = []
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_recent_reboots_summary()
            assert result is not None
            assert "Active reboot" in result

    def test_with_active_failed(self):
        from elle.cli.reboot.commands import render_recent_reboots_summary

        active = SimpleNamespace(
            id="abc123",
            status="failed",
            goal="Kernel update",
        )
        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.return_value = active
        mock_reboot.list_recent_intents.return_value = []
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_recent_reboots_summary()
            assert "failed" in result.lower()

    def test_with_notable_recent(self):
        from elle.cli.reboot.commands import render_recent_reboots_summary

        recent = SimpleNamespace(
            id="def456",
            status="completed",
            outcome="failed",
            goal="Security update",
            created_at=datetime(2024, 3, 15, 10, 0),
        )
        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.return_value = None
        mock_reboot.list_recent_intents.return_value = [recent]
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_recent_reboots_summary()
            assert result is not None
            assert "reboot issues" in result

    def test_exception_returns_none(self):
        from elle.cli.reboot.commands import render_recent_reboots_summary

        mock_reboot = MagicMock()
        mock_reboot.get_active_intent.side_effect = RuntimeError("db error")
        with patch.dict("sys.modules", {"elle.daemon.reboot": mock_reboot}):
            result = render_recent_reboots_summary()
            assert result is None
