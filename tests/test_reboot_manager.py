from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.reboot.manager import (
    RebootManager,
    RebootManagerError,
    _notify_reboot,
    get_manager,
)
from elle.daemon.reboot.models import (
    GRUBState,
    PendingVerification,
    RebootIntent,
)
from elle.daemon.reboot.verifier import VerificationResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MGR = "elle.daemon.reboot.manager"


def _make_intent(**overrides: Any) -> RebootIntent:
    defaults: dict[str, Any] = {
        "id": "intent-001",
        "goal": "Test reboot",
        "task_description": "Testing reboot flow",
        "reason": "user_requested",
        "status": "pending",
        "boot_id": "boot-1",
        "grub_default_saved": "0",
    }
    defaults.update(overrides)
    return RebootIntent(**defaults)


def _make_grub_state(**overrides: Any) -> GRUBState:
    defaults: dict[str, Any] = {
        "default_entry": "0",
        "entries": ("Ubuntu", "Ubuntu Advanced"),
    }
    defaults.update(overrides)
    return GRUBState(**defaults)


def _make_verification_result(
    passed: bool = True,
    critical_failure: bool = False,
    error: str | None = None,
    results: tuple[dict[str, Any], ...] = (),
) -> VerificationResult:
    return VerificationResult(
        passed=passed,
        all_passed=passed and not critical_failure,
        critical_failure=critical_failure,
        required_passed=1 if passed else 0,
        required_failed=0 if passed else 1,
        optional_passed=0,
        optional_failed=0,
        results=results,
        error=error,
    )


def _make_diagnostics(**overrides: Any) -> MagicMock:
    """Build a mock FailureDiagnostics with sensible defaults."""
    diag = MagicMock()
    diag.likely_causes = overrides.get("likely_causes", [])
    diag.failed_services = overrides.get("failed_services", [])
    diag.read_only_mounts = overrides.get("read_only_mounts", [])
    diag.failed_checks = overrides.get("failed_checks", [])
    diag.dmesg_errors = overrides.get("dmesg_errors", [])
    diag.journal_errors = overrides.get("journal_errors", [])
    diag.missing_mounts = overrides.get("missing_mounts", [])
    diag.interfaces_down = overrides.get("interfaces_down", [])
    diag.network_errors = overrides.get("network_errors", [])
    diag.missing_modules = overrides.get("missing_modules", [])
    diag.module_errors = overrides.get("module_errors", [])
    diag.to_llm_context.return_value = "## DIAGNOSTICS"
    return diag


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
        with patch(f"{_MGR}._manager", None):
            mgr = get_manager()
            assert isinstance(mgr, RebootManager)

    def test_returns_same_instance(self):
        with patch(f"{_MGR}._manager", None):
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

        with (
            patch(f"{_MGR}.get_active_intent", return_value=None),
            patch(f"{_MGR}.get_boot_id", return_value="boot-1"),
            patch(f"{_MGR}.get_grub_state", return_value=grub_state),
            patch.object(mgr, "_capture_snapshot", new_callable=AsyncMock, return_value={}),
            patch(f"{_MGR}.create_intent", return_value=intent),
        ):
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

        with patch(f"{_MGR}.get_active_intent", return_value=active):
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

        with (
            patch(f"{_MGR}.get_active_intent", return_value=None),
            patch(f"{_MGR}.get_boot_id", return_value="boot-1"),
            patch(f"{_MGR}.get_grub_state", return_value=grub_state),
            patch.object(mgr, "_capture_snapshot", new_callable=AsyncMock, return_value={}),
            patch(f"{_MGR}.create_intent", return_value=intent) as mock_create,
        ):
            await mgr.create_reboot_intent(
                goal="Test",
                task_description="Testing",
                reason="user_requested",
                verifications=verifications,
            )
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["verifications"] == verifications

    @pytest.mark.asyncio
    async def test_uses_grub_default_when_no_entry(self):
        """When grub_entry is None, the default_entry from grub_state is used."""
        mgr = RebootManager()
        grub_state = _make_grub_state(default_entry="saved")
        intent = _make_intent()

        with (
            patch(f"{_MGR}.get_active_intent", return_value=None),
            patch(f"{_MGR}.get_boot_id", return_value="boot-1"),
            patch(f"{_MGR}.get_grub_state", return_value=grub_state),
            patch.object(mgr, "_capture_snapshot", new_callable=AsyncMock, return_value={}),
            patch(f"{_MGR}.create_intent", return_value=intent) as mock_create,
        ):
            await mgr.create_reboot_intent(
                goal="Test",
                task_description="Testing",
                reason="user_requested",
                grub_entry=None,
            )
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["grub_entry"] == "saved"

    @pytest.mark.asyncio
    async def test_passes_optional_params(self):
        """Ensure optional params are forwarded to create_intent."""
        mgr = RebootManager()
        grub_state = _make_grub_state()
        intent = _make_intent()

        with (
            patch(f"{_MGR}.get_active_intent", return_value=None),
            patch(f"{_MGR}.get_boot_id", return_value="boot-1"),
            patch(f"{_MGR}.get_grub_state", return_value=grub_state),
            patch.object(mgr, "_capture_snapshot", new_callable=AsyncMock, return_value={}),
            patch(f"{_MGR}.create_intent", return_value=intent) as mock_create,
        ):
            await mgr.create_reboot_intent(
                goal="Test",
                task_description="Testing",
                reason="kernel_update",
                reason_detail="New kernel 6.8",
                incident_id="inc-1",
                session_history=["apt upgrade"],
                plan_json={"step": 1},
                grub_entry="1",
            )
            kw = mock_create.call_args[1]
            assert kw["reason_detail"] == "New kernel 6.8"
            assert kw["incident_id"] == "inc-1"
            assert kw["session_history"] == ["apt upgrade"]
            assert kw["plan_json"] == {"step": 1}
            assert kw["grub_entry"] == "1"


# ===========================================================================
# execute_reboot
# ===========================================================================


class TestExecuteReboot:
    @pytest.mark.asyncio
    async def test_intent_not_found_raises(self):
        mgr = RebootManager()
        with patch(f"{_MGR}.get_intent", return_value=None):
            with pytest.raises(RebootManagerError, match="not found"):
                await mgr.execute_reboot("nonexistent")

    @pytest.mark.asyncio
    async def test_wrong_status_raises(self):
        mgr = RebootManager()
        intent = _make_intent(status="completed")
        with patch(f"{_MGR}.get_intent", return_value=intent):
            with pytest.raises(RebootManagerError, match="Cannot execute"):
                await mgr.execute_reboot("intent-001")

    @pytest.mark.asyncio
    async def test_execute_reboot_calls_reboot(self):
        mgr = RebootManager()
        intent = _make_intent(status="pending", grub_entry="1")
        marked = _make_intent(status="rebooting")

        with (
            patch(f"{_MGR}.get_intent", return_value=intent),
            patch(f"{_MGR}.prepare_rollback", new_callable=AsyncMock),
            patch(f"{_MGR}.set_grub_oneshot", new_callable=AsyncMock) as mock_oneshot,
            patch(f"{_MGR}.get_boot_id", return_value="boot-1"),
            patch(f"{_MGR}.mark_rebooting", return_value=marked),
            patch.object(mgr, "_execute_system_reboot", new_callable=AsyncMock) as mock_reboot,
        ):
            mock_oneshot.return_value = MagicMock(success=True)
            await mgr.execute_reboot("intent-001", countdown_seconds=0)
            mock_reboot.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_reboot_with_countdown(self):
        mgr = RebootManager()
        intent = _make_intent(status="pending")
        marked = _make_intent(status="rebooting")
        countdown_calls: list[int] = []

        with (
            patch(f"{_MGR}.get_intent", return_value=intent),
            patch(f"{_MGR}.prepare_rollback", new_callable=AsyncMock),
            patch(
                f"{_MGR}.set_grub_oneshot",
                new_callable=AsyncMock,
                return_value=MagicMock(success=True),
            ),
            patch(f"{_MGR}.get_boot_id", return_value="boot-1"),
            patch(f"{_MGR}.mark_rebooting", return_value=marked),
            patch.object(mgr, "_execute_system_reboot", new_callable=AsyncMock),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
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

        with (
            patch(f"{_MGR}.get_intent", return_value=intent),
            patch(f"{_MGR}.prepare_rollback", new_callable=AsyncMock),
            patch(
                f"{_MGR}.set_grub_oneshot",
                new_callable=AsyncMock,
                return_value=MagicMock(success=True),
            ),
            patch(f"{_MGR}.get_boot_id", return_value="boot-1"),
            patch(f"{_MGR}.mark_rebooting", return_value=None),
        ):
            with pytest.raises(RebootManagerError, match="Failed to mark"):
                await mgr.execute_reboot("intent-001", countdown_seconds=0)

    @pytest.mark.asyncio
    async def test_execute_reboot_grub_oneshot_failure_continues(self):
        mgr = RebootManager()
        intent = _make_intent(status="pending", grub_entry="1")
        marked = _make_intent(status="rebooting")

        with (
            patch(f"{_MGR}.get_intent", return_value=intent),
            patch(f"{_MGR}.prepare_rollback", new_callable=AsyncMock),
            patch(f"{_MGR}.set_grub_oneshot", new_callable=AsyncMock) as mock_oneshot,
            patch(f"{_MGR}.get_boot_id", return_value="boot-1"),
            patch(f"{_MGR}.mark_rebooting", return_value=marked),
            patch.object(mgr, "_execute_system_reboot", new_callable=AsyncMock) as mock_reboot,
        ):
            mock_oneshot.return_value = MagicMock(success=False, error="GRUB error")
            await mgr.execute_reboot("intent-001", countdown_seconds=0)
            # Should still reboot despite GRUB error
            mock_reboot.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_reboot_no_grub_entry_skips_oneshot(self):
        """When grub_entry is None, set_grub_oneshot should not be called."""
        mgr = RebootManager()
        intent = _make_intent(status="pending", grub_entry=None)
        marked = _make_intent(status="rebooting")

        with (
            patch(f"{_MGR}.get_intent", return_value=intent),
            patch(f"{_MGR}.prepare_rollback", new_callable=AsyncMock),
            patch(f"{_MGR}.set_grub_oneshot", new_callable=AsyncMock) as mock_oneshot,
            patch(f"{_MGR}.get_boot_id", return_value="boot-1"),
            patch(f"{_MGR}.mark_rebooting", return_value=marked),
            patch.object(mgr, "_execute_system_reboot", new_callable=AsyncMock),
        ):
            await mgr.execute_reboot("intent-001", countdown_seconds=0)
            mock_oneshot.assert_not_called()


# ===========================================================================
# cancel_pending_reboot
# ===========================================================================


class TestCancelPendingReboot:
    @pytest.mark.asyncio
    async def test_cancel_existing(self):
        mgr = RebootManager()
        intent = _make_intent(status="cancelled")
        with (
            patch(f"{_MGR}.cancel_intent", return_value=intent),
            patch(f"{_MGR}.clear_grub_oneshot", new_callable=AsyncMock),
        ):
            result = await mgr.cancel_pending_reboot("intent-001")
            assert result is not None
            assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self):
        mgr = RebootManager()
        with patch(f"{_MGR}.cancel_intent", return_value=None):
            result = await mgr.cancel_pending_reboot("nonexistent")
            assert result is None

    @pytest.mark.asyncio
    async def test_cancel_clears_grub_oneshot(self):
        mgr = RebootManager()
        intent = _make_intent(status="cancelled")
        with (
            patch(f"{_MGR}.cancel_intent", return_value=intent),
            patch(f"{_MGR}.clear_grub_oneshot", new_callable=AsyncMock) as mock_clear,
        ):
            await mgr.cancel_pending_reboot("intent-001")
            mock_clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_does_not_clear_grub(self):
        mgr = RebootManager()
        with (
            patch(f"{_MGR}.cancel_intent", return_value=None),
            patch(f"{_MGR}.clear_grub_oneshot", new_callable=AsyncMock) as mock_clear,
        ):
            await mgr.cancel_pending_reboot("nonexistent")
            mock_clear.assert_not_called()


# ===========================================================================
# check_pending_reboots
# ===========================================================================


class TestCheckPendingReboots:
    @pytest.mark.asyncio
    async def test_no_pending_reboots(self):
        mgr = RebootManager()
        with patch(f"{_MGR}.get_intents_by_status", return_value=[]):
            result = await mgr.check_pending_reboots()
            assert result is None

    @pytest.mark.asyncio
    async def test_reboot_never_happened(self):
        mgr = RebootManager()
        intent = _make_intent(status="rebooting")
        cancelled = _make_intent(status="cancelled")

        with (
            patch(f"{_MGR}.get_intents_by_status", return_value=[intent]),
            patch(f"{_MGR}.get_boot_id", return_value="boot-2"),
            patch(f"{_MGR}.has_rebooted_since", return_value=False),
            patch(f"{_MGR}.update_intent_status", return_value=cancelled),
        ):
            result = await mgr.check_pending_reboots()
            assert result is not None
            assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_post_reboot_verification(self):
        mgr = RebootManager()
        intent = _make_intent(status="rebooting")
        completed = _make_intent(status="completed", outcome="improved")

        with (
            patch(f"{_MGR}.get_intents_by_status", return_value=[intent]),
            patch(f"{_MGR}.get_boot_id", return_value="boot-2"),
            patch(f"{_MGR}.has_rebooted_since", return_value=True),
            patch(f"{_MGR}.update_intent_status") as mock_update,
            patch.object(mgr, "_capture_snapshot", new_callable=AsyncMock, return_value={}),
            patch(f"{_MGR}.get_intent", return_value=intent),
            patch.object(mgr, "_run_post_boot_verification", new_callable=AsyncMock, return_value=completed),
        ):
            mock_update.return_value = intent
            result = await mgr.check_pending_reboots()
            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_post_reboot_get_intent_returns_none(self):
        """When refreshed intent is None, check_pending_reboots returns None."""
        mgr = RebootManager()
        intent = _make_intent(status="rebooting")

        with (
            patch(f"{_MGR}.get_intents_by_status", return_value=[intent]),
            patch(f"{_MGR}.get_boot_id", return_value="boot-2"),
            patch(f"{_MGR}.has_rebooted_since", return_value=True),
            patch(f"{_MGR}.update_intent_status", return_value=intent),
            patch.object(mgr, "_capture_snapshot", new_callable=AsyncMock, return_value={}),
            patch(f"{_MGR}.get_intent", return_value=None),
        ):
            result = await mgr.check_pending_reboots()
            assert result is None


# ===========================================================================
# _run_post_boot_verification
# ===========================================================================


class TestRunPostBootVerification:
    @pytest.mark.asyncio
    async def test_success_path(self):
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        completed = _make_intent(status="completed")
        vresult = _make_verification_result(
            passed=True,
            results=({"verification_id": 1, "exit_code": 0, "passed": True},),
        )

        with (
            patch(
                f"{_MGR}.run_verification_with_retry",
                new_callable=AsyncMock,
                return_value=(True, vresult),
            ),
            patch(f"{_MGR}.update_verification_result"),
            patch.object(mgr, "_handle_verification_success", new_callable=AsyncMock, return_value=completed),
        ):
            result = await mgr._run_post_boot_verification(intent)
            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_failure_path(self):
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        failed = _make_intent(status="failed")
        vresult = _make_verification_result(passed=False, results=())

        with (
            patch(
                f"{_MGR}.run_verification_with_retry",
                new_callable=AsyncMock,
                return_value=(False, vresult),
            ),
            patch.object(mgr, "_handle_verification_failure", new_callable=AsyncMock, return_value=failed),
        ):
            result = await mgr._run_post_boot_verification(intent)
            assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_updates_verification_results_with_stdout_stderr(self):
        """Ensure each check result updates the DB when it has a verification_id."""
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        completed = _make_intent(status="completed")
        vresult = _make_verification_result(
            passed=True,
            results=(
                {
                    "verification_id": 10,
                    "exit_code": 0,
                    "passed": True,
                    "stdout": "active",
                    "stderr": "",
                },
                {
                    "verification_id": 11,
                    "exit_code": 0,
                    "passed": True,
                    "stdout": "ok",
                    "stderr": None,
                },
            ),
        )

        with (
            patch(
                f"{_MGR}.run_verification_with_retry",
                new_callable=AsyncMock,
                return_value=(True, vresult),
            ),
            patch(f"{_MGR}.update_verification_result") as mock_update,
            patch.object(mgr, "_handle_verification_success", new_callable=AsyncMock, return_value=completed),
        ):
            await mgr._run_post_boot_verification(intent)
            assert mock_update.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_results_without_verification_id(self):
        """Results without a verification_id should not trigger DB update."""
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        completed = _make_intent(status="completed")
        vresult = _make_verification_result(
            passed=True,
            results=({"verification_id": None, "exit_code": 0, "passed": True},),
        )

        with (
            patch(
                f"{_MGR}.run_verification_with_retry",
                new_callable=AsyncMock,
                return_value=(True, vresult),
            ),
            patch(f"{_MGR}.update_verification_result") as mock_update,
            patch.object(mgr, "_handle_verification_success", new_callable=AsyncMock, return_value=completed),
        ):
            await mgr._run_post_boot_verification(intent)
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_result_skips_update(self):
        """When result is None, no update_verification_result calls."""
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        failed = _make_intent(status="failed")

        with (
            patch(
                f"{_MGR}.run_verification_with_retry",
                new_callable=AsyncMock,
                return_value=(False, None),
            ),
            patch(f"{_MGR}.update_verification_result") as mock_update,
            patch.object(mgr, "_handle_verification_failure", new_callable=AsyncMock, return_value=failed),
        ):
            await mgr._run_post_boot_verification(intent)
            mock_update.assert_not_called()


# ===========================================================================
# _handle_verification_success
# ===========================================================================


class TestHandleVerificationSuccess:
    @pytest.mark.asyncio
    async def test_confirms_boot_and_completes(self):
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        completed = _make_intent(status="completed", outcome="improved")

        with (
            patch(f"{_MGR}.confirm_boot_success", new_callable=AsyncMock) as mock_confirm,
            patch(f"{_MGR}.clear_grub_oneshot", new_callable=AsyncMock),
            patch(f"{_MGR}.update_intent_status", return_value=completed),
            patch(f"{_MGR}._notify_reboot"),
        ):
            mock_confirm.return_value = MagicMock(success=True)
            result = await mgr._handle_verification_success(intent)
            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_grub_confirm_failure_continues(self):
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        completed = _make_intent(status="completed")

        with (
            patch(f"{_MGR}.confirm_boot_success", new_callable=AsyncMock) as mock_confirm,
            patch(f"{_MGR}.clear_grub_oneshot", new_callable=AsyncMock),
            patch(f"{_MGR}.update_intent_status", return_value=completed),
            patch(f"{_MGR}._notify_reboot"),
        ):
            mock_confirm.return_value = MagicMock(success=False, error="GRUB error")
            result = await mgr._handle_verification_success(intent)
            # Should still complete
            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_returns_intent_when_update_returns_none(self):
        """If update_intent_status returns None, the original intent is returned."""
        mgr = RebootManager()
        intent = _make_intent(status="verifying")

        with (
            patch(f"{_MGR}.confirm_boot_success", new_callable=AsyncMock) as mock_confirm,
            patch(f"{_MGR}.clear_grub_oneshot", new_callable=AsyncMock),
            patch(f"{_MGR}.update_intent_status", return_value=None),
            patch(f"{_MGR}._notify_reboot"),
        ):
            mock_confirm.return_value = MagicMock(success=True)
            result = await mgr._handle_verification_success(intent)
            assert result is intent


# ===========================================================================
# _handle_verification_failure
# ===========================================================================


class TestHandleVerificationFailure:
    @pytest.mark.asyncio
    async def test_critical_failure_initiates_rollback(self):
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        rolled_back = _make_intent(status="rolled_back")
        result = _make_verification_result(
            passed=False,
            critical_failure=True,
            error="Critical failure",
        )

        with (
            patch(f"{_MGR}.collect_failure_diagnostics", new_callable=AsyncMock) as mock_diag,
            patch.object(mgr, "_create_or_update_failure_incident", new_callable=AsyncMock, return_value="inc-1"),
            patch.object(mgr, "_initiate_rollback", new_callable=AsyncMock, return_value=rolled_back),
        ):
            mock_diag.return_value = _make_diagnostics()
            res = await mgr._handle_verification_failure(intent, result)
            assert res.status == "rolled_back"

    @pytest.mark.asyncio
    async def test_non_critical_failure_starts_rollback_timer(self):
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        failed = _make_intent(status="failed")
        result = _make_verification_result(
            passed=False,
            critical_failure=False,
            error="Check failed",
        )

        with (
            patch(f"{_MGR}.collect_failure_diagnostics", new_callable=AsyncMock) as mock_diag,
            patch.object(mgr, "_create_or_update_failure_incident", new_callable=AsyncMock, return_value=None),
            patch(f"{_MGR}.update_intent_status", return_value=failed),
            patch(f"{_MGR}._notify_reboot"),
            patch(f"{_MGR}.get_intent", return_value=failed),
            patch("asyncio.create_task") as mock_task,
        ):
            mock_diag.return_value = _make_diagnostics(likely_causes=["error"])
            await mgr._handle_verification_failure(intent, result)
            mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_outcome_detail_includes_error(self):
        """The outcome_detail should incorporate result.error, likely_causes, etc."""
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        result = _make_verification_result(
            passed=False,
            critical_failure=True,
            error="Service down",
        )
        diag = _make_diagnostics(
            likely_causes=["service_crash"],
            failed_services=["nginx"],
            read_only_mounts=["/var"],
        )

        with (
            patch(f"{_MGR}.collect_failure_diagnostics", new_callable=AsyncMock, return_value=diag),
            patch.object(mgr, "_create_or_update_failure_incident", new_callable=AsyncMock, return_value=None),
            patch.object(mgr, "_initiate_rollback", new_callable=AsyncMock) as mock_rollback,
        ):
            mock_rollback.return_value = _make_intent(status="rolled_back")
            await mgr._handle_verification_failure(intent, result)

            # Check the outcome_detail passed to _initiate_rollback
            call_args = mock_rollback.call_args
            detail = call_args[0][1]
            assert "Service down" in detail
            assert "service_crash" in detail
            assert "nginx" in detail
            assert "/var" in detail

    @pytest.mark.asyncio
    async def test_outcome_detail_unknown_when_no_parts(self):
        """When result has no error and diagnostics are empty, fallback detail is used."""
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        failed = _make_intent(status="failed")
        result = _make_verification_result(
            passed=False,
            critical_failure=False,
            error=None,
        )
        diag = _make_diagnostics()

        with (
            patch(f"{_MGR}.collect_failure_diagnostics", new_callable=AsyncMock, return_value=diag),
            patch.object(mgr, "_create_or_update_failure_incident", new_callable=AsyncMock, return_value=None),
            patch(f"{_MGR}.update_intent_status") as mock_update,
            patch(f"{_MGR}._notify_reboot"),
            patch(f"{_MGR}.get_intent", return_value=failed),
            patch("asyncio.create_task"),
        ):
            mock_update.return_value = failed
            await mgr._handle_verification_failure(intent, result)
            # Verify the outcome_detail in the update call contains "Unknown failure"
            kw = mock_update.call_args[1]
            assert kw["outcome_detail"] == "Unknown failure"

    @pytest.mark.asyncio
    async def test_stores_diagnostics_on_manager(self):
        """After failure handling, _last_diagnostics is set on the manager."""
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        failed = _make_intent(status="failed")
        result = _make_verification_result(passed=False, critical_failure=False)
        diag = _make_diagnostics(likely_causes=["disk full"])

        with (
            patch(f"{_MGR}.collect_failure_diagnostics", new_callable=AsyncMock, return_value=diag),
            patch.object(mgr, "_create_or_update_failure_incident", new_callable=AsyncMock, return_value=None),
            patch(f"{_MGR}.update_intent_status", return_value=failed),
            patch(f"{_MGR}._notify_reboot"),
            patch(f"{_MGR}.get_intent", return_value=failed),
            patch("asyncio.create_task"),
        ):
            await mgr._handle_verification_failure(intent, result)
            assert mgr._last_diagnostics is diag

    @pytest.mark.asyncio
    async def test_handles_none_result(self):
        """When the verification result is None, it should not crash."""
        mgr = RebootManager()
        intent = _make_intent(status="verifying")
        failed = _make_intent(status="failed")

        with (
            patch(f"{_MGR}.collect_failure_diagnostics", new_callable=AsyncMock) as mock_diag,
            patch.object(mgr, "_create_or_update_failure_incident", new_callable=AsyncMock, return_value=None),
            patch(f"{_MGR}.update_intent_status", return_value=failed),
            patch(f"{_MGR}._notify_reboot"),
            patch(f"{_MGR}.get_intent", return_value=failed),
            patch("asyncio.create_task"),
        ):
            mock_diag.return_value = _make_diagnostics()
            # Pass None result - result.error and result.critical_failure accessed safely
            res = await mgr._handle_verification_failure(intent, None)
            assert res is not None


# ===========================================================================
# _create_or_update_failure_incident
# ===========================================================================


class TestCreateOrUpdateFailureIncident:
    @pytest.mark.asyncio
    async def test_updates_existing_incident(self):
        """When intent has incident_id and the incident exists, append_action is called."""
        mgr = RebootManager()
        intent = _make_intent(incident_id="inc-existing")
        diag = _make_diagnostics(
            failed_checks=[{"check_type": "service_active", "check_command": "nginx"}],
            journal_errors=["Error line 1"],
        )

        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-existing"

        with (
            patch("elle.daemon.incidents.store.get_incident", return_value=mock_incident),
            patch("elle.daemon.incidents.store.append_action") as mock_append,
            patch("elle.daemon.incidents.store.create_incident_draft"),
        ):
            result = await mgr._create_or_update_failure_incident(intent, diag)
            assert result == "inc-existing"
            mock_append.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_new_incident_when_no_incident_id(self):
        """When intent has no incident_id, a new incident is created."""
        mgr = RebootManager()
        intent = _make_intent(incident_id=None)
        diag = _make_diagnostics(
            failed_checks=[],
            journal_errors=[],
        )

        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-new"

        with (
            patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident),
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action"),
            patch("elle.daemon.incidents.snapshot.collect_snapshot", side_effect=RuntimeError("skip")),
        ):
            result = await mgr._create_or_update_failure_incident(intent, diag)
            assert result == "inc-new"

    @pytest.mark.asyncio
    async def test_import_error_returns_none(self):
        """When incident store modules are unavailable, returns None."""
        mgr = RebootManager()
        intent = _make_intent()
        diag = _make_diagnostics()

        with patch.dict("sys.modules", {"elle.daemon.incidents.snapshot": None, "elle.daemon.incidents.store": None}):
            result = await mgr._create_or_update_failure_incident(intent, diag)
            assert result is None

    @pytest.mark.asyncio
    async def test_general_exception_returns_none(self):
        """When incident store raises a generic error, returns None."""
        mgr = RebootManager()
        intent = _make_intent(incident_id=None)
        diag = _make_diagnostics()

        with patch(
            "elle.daemon.incidents.store.create_incident_draft",
            side_effect=RuntimeError("DB down"),
        ):
            result = await mgr._create_or_update_failure_incident(intent, diag)
            assert result is None

    @pytest.mark.asyncio
    async def test_domain_detection_filesystem(self):
        """When diagnostics show read_only_mounts, domain should be 'fs'."""
        mgr = RebootManager()
        intent = _make_intent(incident_id=None)
        diag = _make_diagnostics(read_only_mounts=["/"])

        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-fs"

        with (
            patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident) as mock_create,
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action"),
            patch("elle.daemon.incidents.snapshot.collect_snapshot", side_effect=RuntimeError("skip")),
        ):
            await mgr._create_or_update_failure_incident(intent, diag)
            kw = mock_create.call_args[1]
            assert kw["domain"] == "fs"

    @pytest.mark.asyncio
    async def test_domain_detection_network(self):
        """When diagnostics show interfaces_down, domain should be 'net'."""
        mgr = RebootManager()
        intent = _make_intent(incident_id=None)
        diag = _make_diagnostics(interfaces_down=["eth0: down"])

        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-net"

        with (
            patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident) as mock_create,
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action"),
            patch("elle.daemon.incidents.snapshot.collect_snapshot", side_effect=RuntimeError("skip")),
        ):
            await mgr._create_or_update_failure_incident(intent, diag)
            kw = mock_create.call_args[1]
            assert kw["domain"] == "net"

    @pytest.mark.asyncio
    async def test_domain_detection_driver(self):
        """When diagnostics show missing_modules, domain should be 'driver'."""
        mgr = RebootManager()
        intent = _make_intent(incident_id=None)
        diag = _make_diagnostics(missing_modules=["nvidia"])

        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-drv"

        with (
            patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident) as mock_create,
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action"),
            patch("elle.daemon.incidents.snapshot.collect_snapshot", side_effect=RuntimeError("skip")),
        ):
            await mgr._create_or_update_failure_incident(intent, diag)
            kw = mock_create.call_args[1]
            assert kw["domain"] == "driver"

    @pytest.mark.asyncio
    async def test_domain_detection_kernel(self):
        """When reason is kernel_update and no fs/net/driver issues, domain is 'kernel'."""
        mgr = RebootManager()
        intent = _make_intent(incident_id=None, reason="kernel_update")
        diag = _make_diagnostics()

        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-kern"

        with (
            patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident) as mock_create,
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action"),
            patch("elle.daemon.incidents.snapshot.collect_snapshot", side_effect=RuntimeError("skip")),
        ):
            await mgr._create_or_update_failure_incident(intent, diag)
            kw = mock_create.call_args[1]
            assert kw["domain"] == "kernel"

    @pytest.mark.asyncio
    async def test_attaches_pre_snapshot_when_available(self):
        """When intent has pre_snapshot_json, attach_snapshot is called for 'pre'."""
        mgr = RebootManager()
        intent = _make_intent(incident_id=None, pre_snapshot_json={"kernel": "6.8.0"})
        diag = _make_diagnostics()

        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-snap"

        with (
            patch("elle.daemon.incidents.store.create_incident_draft", return_value=mock_incident),
            patch("elle.daemon.incidents.store.update_incident"),
            patch("elle.daemon.incidents.store.append_action"),
            patch("elle.daemon.incidents.store.attach_snapshot") as mock_attach,
            patch("elle.daemon.incidents.snapshot.collect_snapshot", side_effect=RuntimeError("skip")),
            patch("elle.daemon.incidents.models.SystemSnapshot") as mock_snap_cls,
        ):
            mock_snap_cls.return_value = MagicMock()
            await mgr._create_or_update_failure_incident(intent, diag)
            # The pre-snapshot attachment should have been called
            pre_calls = [c for c in mock_attach.call_args_list if c[0][1] == "pre"]
            assert len(pre_calls) == 1


# ===========================================================================
# force_confirm_boot
# ===========================================================================


class TestForceConfirmBoot:
    @pytest.mark.asyncio
    async def test_confirms_boot(self):
        mgr = RebootManager()
        intent = _make_intent(status="failed")
        completed = _make_intent(status="completed")

        with (
            patch(f"{_MGR}.get_intent", return_value=intent),
            patch.object(mgr, "cancel_pending_rollback", new_callable=AsyncMock, return_value=True),
            patch(f"{_MGR}.confirm_boot_success", new_callable=AsyncMock),
            patch(f"{_MGR}.clear_grub_oneshot", new_callable=AsyncMock),
            patch(f"{_MGR}.update_intent_status", return_value=completed),
        ):
            result = await mgr.force_confirm_boot("intent-001")
            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_intent_not_found(self):
        mgr = RebootManager()
        with patch(f"{_MGR}.get_intent", return_value=None):
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

        with (
            patch(f"{_MGR}.get_intent", return_value=intent),
            patch.object(mgr, "_initiate_rollback", new_callable=AsyncMock, return_value=rolled_back),
        ):
            result = await mgr.force_rollback("intent-001")
            assert result.status == "rolled_back"

    @pytest.mark.asyncio
    async def test_intent_not_found(self):
        mgr = RebootManager()
        with patch(f"{_MGR}.get_intent", return_value=None):
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

        with (
            patch(f"{_MGR}.trigger_rollback_reboot", new_callable=AsyncMock) as mock_trigger,
            patch(f"{_MGR}.update_intent_status", return_value=rolled_back),
            patch(f"{_MGR}._notify_reboot"),
            patch.object(mgr, "_execute_system_reboot", new_callable=AsyncMock),
            patch(f"{_MGR}.get_intent", return_value=rolled_back),
        ):
            mock_trigger.return_value = MagicMock(success=True)
            result = await mgr._initiate_rollback(intent, "test reason")
            assert result.status == "rolled_back"

    @pytest.mark.asyncio
    async def test_rollback_config_failure(self):
        mgr = RebootManager()
        intent = _make_intent(grub_default_saved="0")
        failed = _make_intent(status="failed")

        with (
            patch(f"{_MGR}.trigger_rollback_reboot", new_callable=AsyncMock) as mock_trigger,
            patch(f"{_MGR}.update_intent_status", return_value=failed),
        ):
            mock_trigger.return_value = MagicMock(success=False, error="GRUB write failed")
            result = await mgr._initiate_rollback(intent, "test reason")
            assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_uses_fallback_entry(self):
        mgr = RebootManager()
        intent = _make_intent(grub_default_saved=None)
        rolled_back = _make_intent(status="rolled_back")

        with (
            patch(f"{_MGR}.trigger_rollback_reboot", new_callable=AsyncMock) as mock_trigger,
            patch(f"{_MGR}.update_intent_status", return_value=rolled_back),
            patch(f"{_MGR}._notify_reboot"),
            patch.object(mgr, "_execute_system_reboot", new_callable=AsyncMock),
            patch(f"{_MGR}.get_intent", return_value=rolled_back),
        ):
            mock_trigger.return_value = MagicMock(success=True)
            await mgr._initiate_rollback(intent, "test")
            mock_trigger.assert_called_once_with("0")

    @pytest.mark.asyncio
    async def test_rollback_config_failure_returns_intent_when_update_none(self):
        """When update_intent_status returns None on config failure, the original intent is returned."""
        mgr = RebootManager()
        intent = _make_intent(grub_default_saved="0")

        with (
            patch(f"{_MGR}.trigger_rollback_reboot", new_callable=AsyncMock) as mock_trigger,
            patch(f"{_MGR}.update_intent_status", return_value=None),
        ):
            mock_trigger.return_value = MagicMock(success=False, error="GRUB write failed")
            result = await mgr._initiate_rollback(intent, "test reason")
            # Should return original intent because update_intent_status returned None
            assert result is intent


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
        with patch(f"{_MGR}.get_intents_by_status", return_value=[]):
            result = await mgr.cleanup_stale_intents()
            assert result == 0

    @pytest.mark.asyncio
    async def test_cleans_stale_intents(self):
        mgr = RebootManager()
        stale_intent = _make_intent(
            status="rebooting",
            updated_at=datetime.now(timezone.utc) - timedelta(hours=48),
        )

        with (
            patch(f"{_MGR}.get_intents_by_status", return_value=[stale_intent]),
            patch(f"{_MGR}.update_intent_status") as mock_update,
        ):
            result = await mgr.cleanup_stale_intents()
            assert result == 1
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_clean_recent_intents(self):
        mgr = RebootManager()
        recent_intent = _make_intent(
            status="rebooting",
            updated_at=datetime.now(timezone.utc),
        )

        with (
            patch(f"{_MGR}.get_intents_by_status", return_value=[recent_intent]),
            patch(f"{_MGR}.update_intent_status") as mock_update,
        ):
            result = await mgr.cleanup_stale_intents()
            assert result == 0
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleans_multiple_stale_intents(self):
        mgr = RebootManager()
        old_time = datetime.now(timezone.utc) - timedelta(hours=48)
        intents = [
            _make_intent(id="i1", status="rebooting", updated_at=old_time),
            _make_intent(id="i2", status="rebooting", updated_at=old_time),
            _make_intent(id="i3", status="rebooting", updated_at=datetime.now(timezone.utc)),
        ]

        with (
            patch(f"{_MGR}.get_intents_by_status", return_value=intents),
            patch(f"{_MGR}.update_intent_status") as mock_update,
        ):
            result = await mgr.cleanup_stale_intents()
            assert result == 2
            assert mock_update.call_count == 2


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
# _execute_system_reboot
# ===========================================================================


class TestExecuteSystemReboot:
    @pytest.mark.asyncio
    async def test_successful_reboot(self):
        mgr = RebootManager()
        mock_helper = MagicMock()
        mock_result = MagicMock(success=True)
        mock_helper._run_pkexec = AsyncMock(return_value=mock_result)

        with (
            patch("elle.security.polkit_helper.get_helper", return_value=mock_helper),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            # Should not raise - after successful reboot, sleep awaits
            await mgr._execute_system_reboot()
            mock_helper._run_pkexec.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_reboot_raises(self):
        mgr = RebootManager()
        mock_helper = MagicMock()
        mock_result = MagicMock(success=False, error="Polkit denied")
        mock_helper._run_pkexec = AsyncMock(return_value=mock_result)

        with patch("elle.security.polkit_helper.get_helper", return_value=mock_helper):
            with pytest.raises(RebootManagerError, match="Failed to execute reboot"):
                await mgr._execute_system_reboot()


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

        with (
            patch(f"{_MGR}.AUTO_ROLLBACK_DELAY_SEC", 0.05),
            patch.object(mgr, "_initiate_rollback", new_callable=AsyncMock, return_value=rolled_back),
        ):
            await asyncio.wait_for(mgr._delayed_rollback(intent), timeout=5.0)
            mgr._initiate_rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancelled_by_intervention(self):
        mgr = RebootManager()
        intent = _make_intent()

        with (
            patch(f"{_MGR}.AUTO_ROLLBACK_DELAY_SEC", 10),
            patch.object(mgr, "_initiate_rollback", new_callable=AsyncMock) as mock_rollback,
        ):
            # Set intervention event before delay expires
            async def set_event():
                await asyncio.sleep(0.01)
                mgr._intervention_event.set()

            asyncio.create_task(set_event())
            await mgr._delayed_rollback(intent)
            mock_rollback.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancelled_by_task_cancellation(self):
        """asyncio.CancelledError is caught and logged inside _delayed_rollback."""
        mgr = RebootManager()
        intent = _make_intent()

        with (
            patch(f"{_MGR}.AUTO_ROLLBACK_DELAY_SEC", 100),
            patch.object(mgr, "_initiate_rollback", new_callable=AsyncMock) as mock_rollback,
        ):
            task = asyncio.create_task(mgr._delayed_rollback(intent))
            await asyncio.sleep(0.01)
            task.cancel()
            # CancelledError is caught inside _delayed_rollback, so the task
            # completes normally without propagating the exception.
            await task
            mock_rollback.assert_not_called()
