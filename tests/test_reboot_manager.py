from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.reboot.models import (
    GRUBState,
    PendingVerification,
    RebootIntent,
)
from elle.daemon.reboot.manager import (
    RebootManager,
    RebootManagerError,
    _notify_reboot,
    get_manager,
)
from elle.daemon.reboot.verifier import VerificationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_intent(**overrides: Any) -> RebootIntent:
    defaults = dict(
        id="intent-001",
        goal="Test reboot",
        task_description="Testing reboot flow",
        reason="user_requested",
        status="pending",
        boot_id="boot-1",
        grub_default_saved="0",
    )
    defaults.update(overrides)
    return RebootIntent(**defaults)


def _make_grub_state(**overrides: Any) -> GRUBState:
    defaults = dict(
        default_entry="0",
        entries=("Ubuntu", "Ubuntu Advanced"),
    )
    defaults.update(overrides)
    return GRUBState(**defaults)


# ===========================================================================
# _notify_reboot
# ===========================================================================

class TestNotifyReboot:
    def test_notify_reboot_success(self):
        with patch("elle.daemon.notifications.notify_reboot_status") as mock_notify:
            _notify_reboot("success", "Install updates", "int-1")
            mock_notify.assert_called_once_with("success", "Install updates", "int-1", None)

    def test_notify_reboot_with_details(self):
        with patch("elle.daemon.notifications.notify_reboot_status") as mock_notify:
            _notify_reboot("failed", "Install updates", "int-1", "check failed")
            mock_notify.assert_called_once_with("failed", "Install updates", "int-1", "check failed")

    def test_notify_reboot_import_error(self):
        with patch.dict("sys.modules", {"elle.daemon.notifications": None}):
            # Should not raise (ImportError handled)
            _notify_reboot("success", "goal", "id")

    def test_notify_reboot_general_error(self):
        with patch("elle.daemon.notifications.notify_reboot_status", side_effect=RuntimeError("oops")):
            # Should not raise
            _notify_reboot("success", "goal", "id")


# ===========================================================================
# RebootManagerError
# ===========================================================================

class TestRebootManagerError:
    def test_is_exception(self):
        err = RebootManagerError("test error")
        assert isinstance(err, Exception)
        assert str(err) == "test error"


# ===========================================================================
# get_manager singleton
# ===========================================================================

class TestGetManager:
    def test_returns_manager(self):
        with patch("elle.daemon.reboot.manager._manager", None):
            mgr = get_manager()
            assert isinstance(mgr, RebootManager)

    def test_returns_same_instance(self):
        with patch("elle.daemon.reboot.manager._manager", None):
            mgr1 = get_manager()
            mgr2 = get_manager()
            assert mgr1 is mgr2


# ===========================================================================
# RebootManager.__init__
# ===========================================================================

class TestRebootManagerInit:
    def test_initial_state(self):
        mgr = RebootManager()
        assert mgr._rollback_task is None
        assert not mgr._intervention_event.is_set()


# ===========================================================================
# create_reboot_intent
# ===========================================================================

class TestCreateRebootIntent:
    @pytest.mark.asyncio
    async def test_creates_intent_successfully(self):
        mgr = RebootManager()
        grub_state = _make_grub_state()
        intent = _make_intent()

        with patch("elle.daemon.reboot.manager.get_active_intent", return_value=None), \
             patch("elle.daemon.reboot.manager.get_boot_id", return_value="boot-1"), \
             patch("elle.daemon.reboot.manager.get_grub_state", return_value=grub_state), \
             patch.object(mgr, "_capture_snapshot", new_callable=AsyncMock, return_value={}), \
             patch("elle.daemon.reboot.manager.create_intent", return_value=intent):
            result = await mgr.create_reboot_intent(
                goal="Test reboot",
                task_description="Testing",
                reason="user_requested",
            )
            assert result.id == "intent-001"

    @pytest.mark.asyncio
    async def test_raises_if_active_intent_exists(self):
        mgr = RebootManager()
        active = _make_intent(status="rebooting")

        with patch("elle.daemon.reboot.manager.get_active_intent", return_value=active):
            with pytest.raises(RebootManagerError, match="already in progress"):
                await mgr.create_reboot_intent(
                    goal="Test",
                    task_description="Testing",
                    reason="user_requested",
                )

    @pytest.mark.asyncio
    async def test_passes_verifications(self):
        mgr = RebootManager()
        grub_state = _make_grub_state()
        verifications = [
            PendingVerification(step_index=0, check_type="command", check_command="true"),
        ]
        intent = _make_intent()

        with patch("elle.daemon.reboot.manager.get_active_intent", return_value=None), \
             patch("elle.daemon.reboot.manager.get_boot_id", return_value="boot-1"), \
             patch("elle.daemon.reboot.manager.get_grub_state", return_value=grub_state), \
             patch.object(mgr, "_capture_snapshot", new_callable=AsyncMock, return_value={}), \
             patch("elle.daemon.reboot.manager.create_intent", return_value=intent) as mock_create:
            await mgr.create_reboot_intent(
                goal="Test",
                task_description="Testing",
                reason="user_requested",
                verifications=verifications,
            )
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["verifications"] == verifications


# ===========================================================================
# execute_reboot
# ===========================================================================

class TestExecuteReboot:
    @pytest.mark.asyncio
    async def test_intent_not_found_raises(self):
        mgr = RebootManager()
        with patch("elle.daemon.reboot.manager.get_intent", return_value=None):
            with pytest.raises(RebootManagerError, match="not found"):
                await mgr.execute_reboot("nonexistent")

    @pytest.mark.asyncio
    async def test_wrong_status_raises(self):
        mgr = RebootManager()
        intent = _make_intent(status="completed")
        with patch("elle.daemon.reboot.manager.get_intent", return_value=intent):
            with pytest.raises(RebootManagerError, match="Cannot execute"):
                await mgr.execute_reboot("intent-001")

    @pytest.mark.asyncio
    async def test_execute_reboot_calls_reboot(self):
        mgr = RebootManager()
        intent = _make_intent(status="pending", grub_entry="1")
        marked = _make_intent(status="rebooting")

        with patch("elle.daemon.reboot.manager.get_intent", return_value=intent), \
             patch("elle.daemon.reboot.manager.prepare_rollback", new_callable=AsyncMock), \
             patch("elle.daemon.reboot.manager.set_grub_oneshot", new_callable=AsyncMock) as mock_oneshot, \
             patch("elle.daemon.reboot.manager.get_boot_id", return_value="boot-1"), \
             patch("elle.daemon.reboot.manager.mark_rebooting", return_value=marked), \
             patch.object(mgr, "_execute_system_reboot", new_callable=AsyncMock) as mock_reboot:
            mock_oneshot.return_value = MagicMock(success=True)
            await mgr.execute_reboot("intent-001", countdown_seconds=0)
            mock_reboot.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_reboot_with_countdown(self):
        mgr = RebootManager()
        intent = _make_intent(status="pending")
        marked = _make_intent(status="rebooting")
        countdown_calls = []

        with patch("elle.daemon.reboot.manager.get_intent", return_value=intent), \
             patch("elle.daemon.reboot.manager.prepare_rollback", new_callable=AsyncMock), \
             patch("elle.daemon.reboot.manager.set_grub_oneshot", new_callable=AsyncMock, return_value=MagicMock(success=True)), \
             patch("elle.daemon.reboot.manager.get_boot_id", return_value="boot-1"), \
             patch("elle.daemon.reboot.manager.mark_rebooting", return_value=marked), \
             patch.object(mgr, "_execute_system_reboot", new_callable=AsyncMock), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await mgr.execute_reboot(
                "intent-001",
                countdown_seconds=3,
                on_countdown=lambda s: countdown_calls.append(s),
            )
            assert countdown_calls == [3, 2, 1]

    @pytest.mark.asyncio
    async def test_execute_reboot_mark_rebooting_fails(self):
        mgr = RebootManager()
        intent = _make_intent(status="pending")

        with patch("elle.daemon.reboot.manager.get_intent", return_value=intent), \
             patch("elle.daemon.reboot.manager.prepare_rollback", new_callable=AsyncMock), \
             patch("elle.daemon.reboot.manager.set_grub_oneshot", new_callable=AsyncMock, return_value=MagicMock(success=True)), \
             patch("elle.daemon.reboot.manager.get_boot_id", return_value="boot-1"), \
             patch("elle.daemon.reboot.manager.mark_rebooting", return_value=None):
            with pytest.raises(RebootManagerError, match="Failed to mark"):
                await mgr.execute_reboot("intent-001", countdown_seconds=0)

    @pytest.mark.asyncio
    async def test_execute_reboot_grub_oneshot_failure_continues(self):
        mgr = RebootManager()
        intent = _make_intent(status="pending", grub_entry="1")
        marked = _make_intent(status="rebooting")

        with patch("elle.daemon.reboot.manager.get_intent", return_value=intent), \
             patch("elle.daemon.reboot.manager.prepare_rollback", new_callable=AsyncMock), \
             patch("elle.daemon.reboot.manager.set_grub_oneshot", new_callable=AsyncMock) as mock_oneshot, \
             patch("elle.daemon.reboot.manager.get_boot_id", return_value="boot-1"), \
             patch("elle.daemon.reboot.manager.mark_rebooting", return_value=marked), \
             patch.object(mgr, "_execute_system_reboot", new_callable=AsyncMock) as mock_reboot:
            mock_oneshot.return_value = MagicMock(success=False, error="GRUB error")
            await mgr.execute_reboot("intent-001", countdown_seconds=0)
            # Should still reboot despite GRUB error
            mock_reboot.assert_called_once()


# ===========================================================================
# cancel_pending_reboot
# ===========================================================================

class TestCancelPendingReboot:
    @pytest.mark.asyncio
    async def test_cancel_existing(self):
        mgr = RebootManager()
        intent = _make_intent(status="cancelled")
        with patch("elle.daemon.reboot.manager.cancel_intent", return_value=intent), \
             patch("elle.daemon.reboot.manager.clear_grub_oneshot", new_callable=AsyncMock):
            result = await mgr.cancel_pending_reboot("intent-001")
            assert result is not None
            assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self):
        mgr = RebootManager()
        with patch("elle.daemon.reboot.manager.cancel_intent", return_value=None):
            result = await mgr.cancel_pending_reboot("nonexistent")
            assert result is None


# ===========================================================================
# check_pending_reboots
# ===========================================================================

class TestCheckPendingReboots:
    @pytest.mark.asyncio
    async def test_no_pending_reboots(self):
        mgr = RebootManager()
        with patch("elle.daemon.reboot.manager.get_intents_by_status", return_value=[]):
            result = await mgr.check_pending_reboots()
            assert result is None

    @pytest.mark.asyncio
    async def test_reboot_never_happened(self):
        mgr = RebootManager()
        intent = _make_intent(status="rebooting")
        cancelled = _make_intent(status="cancelled")

        with patch("elle.daemon.reboot.manager.get_intents_by_status", return_value=[intent]), \
             patch("elle.daemon.reboot.manager.get_boot_id", return_value="boot-2"), \
             patch("elle.daemon.reboot.manager.has_rebooted_since", return_value=False), \
             patch("elle.daemon.reboot.manager.update_intent_status", return_value=cancelled):
            result = await mgr.check_pending_reboots()
            assert result is not None
            assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_post_reboot_verification(self):
        mgr = RebootManager()
        intent = _make_intent(status="rebooting")
        completed = _make_intent(status="completed", outcome="improved")

        with patch("elle.daemon.reboot.manager.get_intents_by_status", return_value=[intent]), \
             patch("elle.daemon.reboot.manager.get_boot_id", return_value="boot-2"), \
             patch("elle.daemon.reboot.manager.has_rebooted_since", return_value=True), \
             patch("elle.daemon.reboot.manager.update_intent_status") as mock_update, \
             patch.object(mgr, "_capture_snapshot", new_callable=AsyncMock, return_value={}), \
             patch("elle.daemon.reboot.manager.get_intent", return_value=intent), \
             patch.object(mgr, "_run_post_boot_verification", new_callable=AsyncMock, return_value=completed):
            mock_update.return_value = intent
            result = await mgr.check_pending_reboots()
            assert result.status == "completed"


# ===========================================================================
# _handle_verification_success
# ===========================================================================

class TestHandleVerificationSuccess:
    @pytest.mark.asyncio
    async def test_confirms_boot_and_completes(self):
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        completed = _make_intent(status="completed", outcome="improved")

        with patch("elle.daemon.reboot.manager.confirm_boot_success", new_callable=AsyncMock) as mock_confirm, \
             patch("elle.daemon.reboot.manager.clear_grub_oneshot", new_callable=AsyncMock), \
             patch("elle.daemon.reboot.manager.update_intent_status", return_value=completed), \
             patch("elle.daemon.reboot.manager._notify_reboot"):
            mock_confirm.return_value = MagicMock(success=True)
            result = await mgr._handle_verification_success(intent)
            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_grub_confirm_failure_continues(self):
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        completed = _make_intent(status="completed")

        with patch("elle.daemon.reboot.manager.confirm_boot_success", new_callable=AsyncMock) as mock_confirm, \
             patch("elle.daemon.reboot.manager.clear_grub_oneshot", new_callable=AsyncMock), \
             patch("elle.daemon.reboot.manager.update_intent_status", return_value=completed), \
             patch("elle.daemon.reboot.manager._notify_reboot"):
            mock_confirm.return_value = MagicMock(success=False, error="GRUB error")
            result = await mgr._handle_verification_success(intent)
            # Should still complete
            assert result.status == "completed"


# ===========================================================================
# _handle_verification_failure
# ===========================================================================

class TestHandleVerificationFailure:
    @pytest.mark.asyncio
    async def test_critical_failure_initiates_rollback(self):
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        rolled_back = _make_intent(status="rolled_back")
        result = VerificationResult(
            passed=False, all_passed=False, critical_failure=True,
            required_passed=0, required_failed=1,
            optional_passed=0, optional_failed=0,
            error="Critical failure",
        )

        with patch("elle.daemon.reboot.manager.collect_failure_diagnostics", new_callable=AsyncMock) as mock_diag, \
             patch.object(mgr, "_create_or_update_failure_incident", new_callable=AsyncMock, return_value="inc-1"), \
             patch.object(mgr, "_initiate_rollback", new_callable=AsyncMock, return_value=rolled_back):
            mock_diag.return_value = MagicMock(likely_causes=[], failed_services=[], read_only_mounts=[])
            res = await mgr._handle_verification_failure(intent, result)
            assert res.status == "rolled_back"

    @pytest.mark.asyncio
    async def test_non_critical_failure_starts_rollback_timer(self):
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        failed = _make_intent(status="failed")
        result = VerificationResult(
            passed=False, all_passed=False, critical_failure=False,
            required_passed=0, required_failed=1,
            optional_passed=0, optional_failed=0,
            error="Check failed",
        )

        with patch("elle.daemon.reboot.manager.collect_failure_diagnostics", new_callable=AsyncMock) as mock_diag, \
             patch.object(mgr, "_create_or_update_failure_incident", new_callable=AsyncMock, return_value=None), \
             patch("elle.daemon.reboot.manager.update_intent_status", return_value=failed), \
             patch("elle.daemon.reboot.manager._notify_reboot"), \
             patch("elle.daemon.reboot.manager.get_intent", return_value=failed), \
             patch("asyncio.create_task") as mock_task:
            mock_diag.return_value = MagicMock(likely_causes=["error"], failed_services=[], read_only_mounts=[])
            res = await mgr._handle_verification_failure(intent, result)
            mock_task.assert_called_once()


# ===========================================================================
# _run_post_boot_verification
# ===========================================================================

class TestRunPostBootVerification:
    @pytest.mark.asyncio
    async def test_success_path(self):
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        completed = _make_intent(status="completed")
        vresult = VerificationResult(
            passed=True, all_passed=True, critical_failure=False,
            required_passed=1, required_failed=0,
            optional_passed=0, optional_failed=0,
            results=({"verification_id": 1, "exit_code": 0, "passed": True},),
        )

        with patch("elle.daemon.reboot.manager.run_verification_with_retry", new_callable=AsyncMock, return_value=(True, vresult)), \
             patch("elle.daemon.reboot.manager.update_verification_result"), \
             patch.object(mgr, "_handle_verification_success", new_callable=AsyncMock, return_value=completed):
            result = await mgr._run_post_boot_verification(intent)
            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_failure_path(self):
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        failed = _make_intent(status="failed")
        vresult = VerificationResult(
            passed=False, all_passed=False, critical_failure=False,
            required_passed=0, required_failed=1,
            optional_passed=0, optional_failed=0,
            results=(),
        )

        with patch("elle.daemon.reboot.manager.run_verification_with_retry", new_callable=AsyncMock, return_value=(False, vresult)), \
             patch.object(mgr, "_handle_verification_failure", new_callable=AsyncMock, return_value=failed):
            result = await mgr._run_post_boot_verification(intent)
            assert result.status == "failed"


# ===========================================================================
# force_confirm_boot
# ===========================================================================

class TestForceConfirmBoot:
    @pytest.mark.asyncio
    async def test_confirms_boot(self):
        mgr = RebootManager()
        intent = _make_intent(status="failed")
        completed = _make_intent(status="completed")

        with patch("elle.daemon.reboot.manager.get_intent", return_value=intent), \
             patch.object(mgr, "cancel_pending_rollback", new_callable=AsyncMock, return_value=True), \
             patch("elle.daemon.reboot.manager.confirm_boot_success", new_callable=AsyncMock), \
             patch("elle.daemon.reboot.manager.clear_grub_oneshot", new_callable=AsyncMock), \
             patch("elle.daemon.reboot.manager.update_intent_status", return_value=completed):
            result = await mgr.force_confirm_boot("intent-001")
            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_intent_not_found(self):
        mgr = RebootManager()
        with patch("elle.daemon.reboot.manager.get_intent", return_value=None):
            result = await mgr.force_confirm_boot("nonexistent")
            assert result is None


# ===========================================================================
# force_rollback
# ===========================================================================

class TestForceRollback:
    @pytest.mark.asyncio
    async def test_initiates_rollback(self):
        mgr = RebootManager()
        intent = _make_intent(status="failed")
        rolled_back = _make_intent(status="rolled_back")

        with patch("elle.daemon.reboot.manager.get_intent", return_value=intent), \
             patch.object(mgr, "_initiate_rollback", new_callable=AsyncMock, return_value=rolled_back):
            result = await mgr.force_rollback("intent-001")
            assert result.status == "rolled_back"

    @pytest.mark.asyncio
    async def test_intent_not_found(self):
        mgr = RebootManager()
        with patch("elle.daemon.reboot.manager.get_intent", return_value=None):
            result = await mgr.force_rollback("nonexistent")
            assert result is None


# ===========================================================================
# _initiate_rollback
# ===========================================================================

class TestInitiateRollback:
    @pytest.mark.asyncio
    async def test_successful_rollback(self):
        mgr = RebootManager()
        intent = _make_intent(grub_default_saved="0")
        rolled_back = _make_intent(status="rolled_back")

        with patch("elle.daemon.reboot.manager.trigger_rollback_reboot", new_callable=AsyncMock) as mock_trigger, \
             patch("elle.daemon.reboot.manager.update_intent_status", return_value=rolled_back), \
             patch("elle.daemon.reboot.manager._notify_reboot"), \
             patch.object(mgr, "_execute_system_reboot", new_callable=AsyncMock), \
             patch("elle.daemon.reboot.manager.get_intent", return_value=rolled_back):
            mock_trigger.return_value = MagicMock(success=True)
            result = await mgr._initiate_rollback(intent, "test reason")
            assert result.status == "rolled_back"

    @pytest.mark.asyncio
    async def test_rollback_config_failure(self):
        mgr = RebootManager()
        intent = _make_intent(grub_default_saved="0")
        failed = _make_intent(status="failed")

        with patch("elle.daemon.reboot.manager.trigger_rollback_reboot", new_callable=AsyncMock) as mock_trigger, \
             patch("elle.daemon.reboot.manager.update_intent_status", return_value=failed):
            mock_trigger.return_value = MagicMock(success=False, error="GRUB write failed")
            result = await mgr._initiate_rollback(intent, "test reason")
            assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_uses_fallback_entry(self):
        mgr = RebootManager()
        intent = _make_intent(grub_default_saved=None)
        rolled_back = _make_intent(status="rolled_back")

        with patch("elle.daemon.reboot.manager.trigger_rollback_reboot", new_callable=AsyncMock) as mock_trigger, \
             patch("elle.daemon.reboot.manager.update_intent_status", return_value=rolled_back), \
             patch("elle.daemon.reboot.manager._notify_reboot"), \
             patch.object(mgr, "_execute_system_reboot", new_callable=AsyncMock), \
             patch("elle.daemon.reboot.manager.get_intent", return_value=rolled_back):
            mock_trigger.return_value = MagicMock(success=True)
            await mgr._initiate_rollback(intent, "test")
            mock_trigger.assert_called_once_with("0")


# ===========================================================================
# cancel_pending_rollback
# ===========================================================================

class TestCancelPendingRollback:
    @pytest.mark.asyncio
    async def test_cancel_with_active_task(self):
        mgr = RebootManager()
        mgr._rollback_task = MagicMock()
        mgr._rollback_task.done.return_value = False

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await mgr.cancel_pending_rollback()
            assert result is True
            assert mgr._intervention_event.is_set() is False  # Should be cleared

    @pytest.mark.asyncio
    async def test_cancel_with_no_task(self):
        mgr = RebootManager()
        result = await mgr.cancel_pending_rollback()
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_with_completed_task(self):
        mgr = RebootManager()
        mgr._rollback_task = MagicMock()
        mgr._rollback_task.done.return_value = True
        result = await mgr.cancel_pending_rollback()
        assert result is False


# ===========================================================================
# cleanup_stale_intents
# ===========================================================================

class TestCleanupStaleIntents:
    @pytest.mark.asyncio
    async def test_no_stale_intents(self):
        mgr = RebootManager()
        with patch("elle.daemon.reboot.manager.get_intents_by_status", return_value=[]):
            result = await mgr.cleanup_stale_intents()
            assert result == 0

    @pytest.mark.asyncio
    async def test_cleans_stale_intents(self):
        mgr = RebootManager()
        stale_intent = _make_intent(
            status="rebooting",
            updated_at=datetime.utcnow() - timedelta(hours=48),
        )

        with patch("elle.daemon.reboot.manager.get_intents_by_status", return_value=[stale_intent]), \
             patch("elle.daemon.reboot.manager.update_intent_status") as mock_update:
            result = await mgr.cleanup_stale_intents()
            assert result == 1
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_clean_recent_intents(self):
        mgr = RebootManager()
        recent_intent = _make_intent(
            status="rebooting",
            updated_at=datetime.utcnow(),
        )

        with patch("elle.daemon.reboot.manager.get_intents_by_status", return_value=[recent_intent]), \
             patch("elle.daemon.reboot.manager.update_intent_status") as mock_update:
            result = await mgr.cleanup_stale_intents()
            assert result == 0
            mock_update.assert_not_called()


# ===========================================================================
# _capture_snapshot
# ===========================================================================

class TestCaptureSnapshot:
    @pytest.mark.asyncio
    async def test_captures_snapshot(self):
        mgr = RebootManager()
        mock_snapshot = MagicMock()
        mock_snapshot.model_dump.return_value = {"kernel": "6.8.0", "os": "Ubuntu"}

        with patch("elle.daemon.incidents.snapshot.collect_snapshot", return_value=mock_snapshot):
            result = await mgr._capture_snapshot()
            assert result == {"kernel": "6.8.0", "os": "Ubuntu"}

    @pytest.mark.asyncio
    async def test_import_error_returns_empty(self):
        with patch.dict("sys.modules", {"elle.daemon.incidents.snapshot": None}):
            mgr = RebootManager()
            result = await mgr._capture_snapshot()
            assert result == {}

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        mgr = RebootManager()
        with patch("elle.daemon.incidents.snapshot.collect_snapshot", side_effect=RuntimeError("fail")):
            result = await mgr._capture_snapshot()
            assert result == {}


# ===========================================================================
# get_last_diagnostics / get_llm_retry_context
# ===========================================================================

class TestDiagnosticsAccess:
    def test_get_last_diagnostics_none_initially(self):
        mgr = RebootManager()
        assert mgr.get_last_diagnostics() is None

    def test_get_last_diagnostics_returns_stored(self):
        mgr = RebootManager()
        mgr._last_diagnostics = MagicMock()
        assert mgr.get_last_diagnostics() is not None

    def test_get_llm_retry_context_none(self):
        mgr = RebootManager()
        assert mgr.get_llm_retry_context() is None

    def test_get_llm_retry_context_with_diagnostics(self):
        mgr = RebootManager()
        mock_diag = MagicMock()
        mock_diag.to_llm_context.return_value = "## FAILURE DIAGNOSTICS\nGoal: test"
        mgr._last_diagnostics = mock_diag
        ctx = mgr.get_llm_retry_context()
        assert "FAILURE DIAGNOSTICS" in ctx

    def test_get_llm_retry_context_returns_none_if_context_is_none(self):
        mgr = RebootManager()
        mock_diag = MagicMock()
        mock_diag.to_llm_context.return_value = None
        mgr._last_diagnostics = mock_diag
        ctx = mgr.get_llm_retry_context()
        assert ctx is None


# ===========================================================================
# _delayed_rollback
# ===========================================================================

class TestDelayedRollback:
    @pytest.mark.asyncio
    async def test_rollback_on_timeout(self):
        mgr = RebootManager()
        intent = _make_intent()
        rolled_back = _make_intent(status="rolled_back")

        with patch("elle.daemon.reboot.manager.AUTO_ROLLBACK_DELAY_SEC", 0.01), \
             patch.object(mgr, "_initiate_rollback", new_callable=AsyncMock, return_value=rolled_back):
            await mgr._delayed_rollback(intent)
            mgr._initiate_rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancelled_by_intervention(self):
        mgr = RebootManager()
        intent = _make_intent()

        with patch("elle.daemon.reboot.manager.AUTO_ROLLBACK_DELAY_SEC", 10), \
             patch.object(mgr, "_initiate_rollback", new_callable=AsyncMock) as mock_rollback:
            # Set intervention event before delay expires
            async def set_event():
                await asyncio.sleep(0.01)
                mgr._intervention_event.set()

            asyncio.create_task(set_event())
            await mgr._delayed_rollback(intent)
            mock_rollback.assert_not_called()
