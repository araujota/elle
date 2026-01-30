from __future__ import annotations

import asyncio
import subprocess
import time
from unittest.mock import MagicMock, AsyncMock, patch, call

import pytest

from elle.daemon.notifications.models import (
    Notification,
    NotificationAction,
    NotificationCategory,
    NotificationResult,
    NotificationUrgency,
)
from elle.daemon.notifications.service import (
    APP_NAME,
    NotificationService,
    RATE_LIMIT_SECONDS,
    _cancel_reboot,
    _create_action_callback,
    _find_terminal,
    _handle_notify_send_action,
    _notification_history,
    _open_elle_repl,
    _push_to_mobile,
    _send_via_notify_send,
    _should_send,
    get_service,
    notify,
    notify_error,
    notify_health,
    notify_incident,
    notify_info,
    notify_job_complete,
    notify_reboot_status,
    send,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_notification(**overrides) -> Notification:
    defaults = dict(
        title="Test Title",
        body="Test body",
        category=NotificationCategory.INFO,
    )
    defaults.update(overrides)
    return Notification(**defaults)


@pytest.fixture(autouse=True)
def clear_rate_limit_history():
    """Clear notification rate-limit history before each test."""
    _notification_history.clear()
    yield
    _notification_history.clear()


@pytest.fixture(autouse=True)
def clear_terminal_cache():
    """Clear lru_cache for _find_terminal before each test."""
    _find_terminal.cache_clear()
    yield
    _find_terminal.cache_clear()


# ===========================================================================
# _should_send (rate limiting)
# ===========================================================================

class TestShouldSend:
    def test_first_notification_allowed(self):
        n = _make_notification()
        assert _should_send(n) is True

    def test_duplicate_within_rate_limit_blocked(self):
        n = _make_notification()
        _should_send(n)
        assert _should_send(n) is False

    def test_different_notifications_allowed(self):
        n1 = _make_notification(title="First")
        n2 = _make_notification(title="Second")
        assert _should_send(n1) is True
        assert _should_send(n2) is True

    def test_after_rate_limit_allowed(self):
        n = _make_notification()
        _should_send(n)
        # Manually set the history to far in the past
        key = f"{n.category}:{n.title}:{n.body[:50]}"
        _notification_history[key] = time.time() - RATE_LIMIT_SECONDS - 1
        assert _should_send(n) is True

    def test_old_entries_cleaned(self):
        # Add an old entry
        _notification_history["old:key:value"] = time.time() - 120
        n = _make_notification()
        _should_send(n)
        assert "old:key:value" not in _notification_history


# ===========================================================================
# _send_via_notify_send
# ===========================================================================

class TestSendViaNotifySend:
    def test_notify_send_not_found(self):
        with patch("shutil.which", return_value=None):
            n = _make_notification()
            result = _send_via_notify_send(n)
            assert result.success is False
            assert "not found" in result.error

    def test_notify_send_success(self):
        with patch("shutil.which", return_value="/usr/bin/notify-send"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            n = _make_notification()
            result = _send_via_notify_send(n)
            assert result.success is True
            assert result.method == "notify-send"

    def test_notify_send_failure(self):
        with patch("shutil.which", return_value="/usr/bin/notify-send"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="display error")
            n = _make_notification()
            result = _send_via_notify_send(n)
            assert result.success is False

    def test_notify_send_timeout(self):
        with patch("shutil.which", return_value="/usr/bin/notify-send"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)):
            n = _make_notification()
            result = _send_via_notify_send(n)
            assert result.success is False
            assert "timed out" in result.error

    def test_notify_send_exception(self):
        with patch("shutil.which", return_value="/usr/bin/notify-send"), \
             patch("subprocess.run", side_effect=OSError("failed")):
            n = _make_notification()
            result = _send_via_notify_send(n)
            assert result.success is False

    def test_notify_send_with_actions(self):
        with patch("shutil.which", return_value="/usr/bin/notify-send"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            n = _make_notification(
                actions=(NotificationAction(id="open", label="Open"),),
                timeout_ms=10000,
            )
            result = _send_via_notify_send(n)
            assert result.success is True

    def test_notify_send_with_action_response(self):
        with patch("shutil.which", return_value="/usr/bin/notify-send"), \
             patch("subprocess.run") as mock_run, \
             patch("elle.daemon.notifications.service._handle_notify_send_action") as mock_handle:
            mock_run.return_value = MagicMock(returncode=0, stdout="open\n", stderr="")
            n = _make_notification()
            _send_via_notify_send(n)
            mock_handle.assert_called_once_with("open", n)

    def test_notify_send_empty_stderr_fallback(self):
        with patch("shutil.which", return_value="/usr/bin/notify-send"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=2, stdout="", stderr="")
            n = _make_notification()
            result = _send_via_notify_send(n)
            assert result.success is False
            assert "Exit code" in result.error


# ===========================================================================
# _handle_notify_send_action
# ===========================================================================

class TestHandleNotifySendAction:
    def test_open_action(self):
        n = _make_notification(context_type="incident", context_id="inc-1")
        with patch("elle.daemon.notifications.service._open_elle_repl") as mock_open:
            _handle_notify_send_action("open", n)
            mock_open.assert_called_once_with("incident", "inc-1")

    def test_investigate_action(self):
        n = _make_notification(context_type="incident", context_id="inc-2")
        with patch("elle.daemon.notifications.service._open_elle_repl") as mock_open:
            _handle_notify_send_action("investigate", n)
            mock_open.assert_called_once()

    def test_confirm_reboot_action(self):
        n = _make_notification(context_type="reboot", context_id="int-1")
        with patch("elle.daemon.notifications.service._open_elle_repl") as mock_open:
            _handle_notify_send_action("confirm", n)
            mock_open.assert_called_once_with("reboot", "int-1")

    def test_execute_plan_action(self):
        n = _make_notification(context_id="inc-3")
        with patch("elle.daemon.notifications.service._execute_forecast_plan") as mock_exec:
            _handle_notify_send_action("execute_plan", n)
            mock_exec.assert_called_once_with("inc-3")

    def test_unhandled_action_does_nothing(self):
        n = _make_notification()
        # Should not raise
        _handle_notify_send_action("unknown", n)


# ===========================================================================
# _find_terminal
# ===========================================================================

class TestFindTerminal:
    def test_finds_available_terminal(self):
        with patch("shutil.which", side_effect=lambda name: "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None):
            result = _find_terminal()
            assert result is not None
            assert result[0] == "gnome-terminal"

    def test_no_terminal_found(self):
        with patch("shutil.which", return_value=None):
            result = _find_terminal()
            assert result is None

    def test_falls_back_to_later_terminals(self):
        def mock_which(name):
            if name == "kitty":
                return "/usr/bin/kitty"
            return None
        with patch("shutil.which", side_effect=mock_which):
            result = _find_terminal()
            assert result is not None
            assert result[0] == "kitty"


# ===========================================================================
# _open_elle_repl
# ===========================================================================

class TestOpenElleRepl:
    def test_no_terminal_returns_false(self):
        with patch("elle.daemon.notifications.service._find_terminal", return_value=None):
            result = _open_elle_repl()
            assert result is False

    def test_opens_gnome_terminal(self):
        with patch("elle.daemon.notifications.service._find_terminal", return_value=("gnome-terminal", ["gnome-terminal", "--", "elle"])), \
             patch("subprocess.Popen") as mock_popen:
            result = _open_elle_repl()
            assert result is True
            mock_popen.assert_called_once()

    def test_opens_with_incident_context(self):
        with patch("elle.daemon.notifications.service._find_terminal", return_value=("gnome-terminal", ["gnome-terminal", "--", "elle"])), \
             patch("subprocess.Popen") as mock_popen:
            _open_elle_repl(context_type="incident", context_id="inc-1")
            args = mock_popen.call_args[0][0]
            assert "incident" in args
            assert "inc-1" in args

    def test_opens_with_reboot_context(self):
        with patch("elle.daemon.notifications.service._find_terminal", return_value=("konsole", ["konsole", "-e", "elle"])), \
             patch("subprocess.Popen") as mock_popen:
            _open_elle_repl(context_type="reboot", context_id="int-1")
            args = mock_popen.call_args[0][0]
            assert "reboot" in args

    def test_opens_kitty_terminal(self):
        with patch("elle.daemon.notifications.service._find_terminal", return_value=("kitty", ["kitty", "elle"])), \
             patch("subprocess.Popen") as mock_popen:
            _open_elle_repl()
            assert mock_popen.called

    def test_opens_alacritty_terminal(self):
        with patch("elle.daemon.notifications.service._find_terminal", return_value=("alacritty", ["alacritty", "-e", "elle"])), \
             patch("subprocess.Popen") as mock_popen:
            _open_elle_repl()
            assert mock_popen.called

    def test_opens_generic_terminal(self):
        with patch("elle.daemon.notifications.service._find_terminal", return_value=("xterm", ["xterm", "-e", "elle"])), \
             patch("subprocess.Popen") as mock_popen:
            _open_elle_repl()
            assert mock_popen.called

    def test_popen_failure_returns_false(self):
        with patch("elle.daemon.notifications.service._find_terminal", return_value=("gnome-terminal", ["gnome-terminal", "--", "elle"])), \
             patch("subprocess.Popen", side_effect=OSError("fail")):
            result = _open_elle_repl()
            assert result is False

    def test_sets_display_env(self):
        import os
        env_copy = os.environ.copy()
        env_copy.pop("DISPLAY", None)
        with patch("elle.daemon.notifications.service._find_terminal", return_value=("gnome-terminal", ["gnome-terminal", "--", "elle"])), \
             patch("subprocess.Popen") as mock_popen, \
             patch.dict(os.environ, {}, clear=True):
            _open_elle_repl()
            if mock_popen.called:
                kw = mock_popen.call_args[1]
                assert "env" in kw


# ===========================================================================
# send (public API with fallback)
# ===========================================================================

class TestSend:
    def test_rate_limited_notification(self):
        n = _make_notification()
        with patch("elle.daemon.notifications.service._should_send", return_value=False):
            result = send(n)
            assert result.success is True
            assert result.method == "rate_limit"

    def test_fallback_to_notify_send(self):
        with patch("elle.daemon.notifications.service._gi_available", False), \
             patch("elle.daemon.notifications.service._push_to_mobile"), \
             patch("elle.daemon.notifications.service._send_via_notify_send") as mock_ns:
            mock_ns.return_value = NotificationResult(success=True, method="notify-send")
            n = _make_notification()
            result = send(n)
            assert result.success is True
            mock_ns.assert_called_once()

    def test_mobile_push_called(self):
        with patch("elle.daemon.notifications.service._gi_available", False), \
             patch("elle.daemon.notifications.service._push_to_mobile") as mock_mobile, \
             patch("elle.daemon.notifications.service._send_via_notify_send") as mock_ns:
            mock_ns.return_value = NotificationResult(success=True, method="notify-send")
            n = _make_notification()
            send(n)
            mock_mobile.assert_called_once_with(n)


# ===========================================================================
# notify convenience function
# ===========================================================================

class TestNotify:
    def test_basic_notify(self):
        with patch("elle.daemon.notifications.service.send") as mock_send:
            mock_send.return_value = NotificationResult(success=True, method="test")
            result = notify("Hello", "World")
            assert result.success is True
            call_args = mock_send.call_args[0][0]
            assert call_args.title == "Hello"
            assert call_args.body == "World"

    def test_notify_with_actions(self):
        with patch("elle.daemon.notifications.service.send") as mock_send:
            mock_send.return_value = NotificationResult(success=True, method="test")
            actions = [NotificationAction(id="open", label="Open")]
            notify("Test", "Body", actions=actions)
            call_args = mock_send.call_args[0][0]
            assert len(call_args.actions) == 1
            assert call_args.default_action == "open"

    def test_notify_without_actions(self):
        with patch("elle.daemon.notifications.service.send") as mock_send:
            mock_send.return_value = NotificationResult(success=True, method="test")
            notify("Test", "Body")
            call_args = mock_send.call_args[0][0]
            assert call_args.default_action is None


# ===========================================================================
# Convenience functions
# ===========================================================================

class TestConvenienceFunctions:
    def test_notify_job_complete_success(self):
        with patch("elle.daemon.notifications.service.send") as mock_send:
            mock_send.return_value = NotificationResult(success=True, method="test")
            result = notify_job_complete("kernel update", success=True)
            assert result.success is True

    def test_notify_job_complete_failure(self):
        with patch("elle.daemon.notifications.service.send") as mock_send:
            mock_send.return_value = NotificationResult(success=True, method="test")
            notify_job_complete("kernel update", success=False, details="package error")
            assert mock_send.called

    def test_notify_reboot_status(self):
        with patch("elle.daemon.notifications.service.send") as mock_send:
            mock_send.return_value = NotificationResult(success=True, method="test")
            result = notify_reboot_status("success", "Install updates", "int-1")
            assert result.success is True

    def test_notify_error(self):
        with patch("elle.daemon.notifications.service.send") as mock_send:
            mock_send.return_value = NotificationResult(success=True, method="test")
            result = notify_error("Error Title", "Error message")
            assert result.success is True

    def test_notify_info(self):
        with patch("elle.daemon.notifications.service.send") as mock_send:
            mock_send.return_value = NotificationResult(success=True, method="test")
            result = notify_info("Info Title", "Info message")
            assert result.success is True

    def test_notify_incident(self):
        with patch("elle.daemon.notifications.service.send") as mock_send:
            mock_send.return_value = NotificationResult(success=True, method="test")
            result = notify_incident("Disk Full", "warning", "disk", "inc-1", "Disk at 95%")
            assert result.success is True

    def test_notify_health(self):
        with patch("elle.daemon.notifications.service.send") as mock_send:
            mock_send.return_value = NotificationResult(success=True, method="test")
            result = notify_health("disk", "Low disk space", severity="warning")
            assert result.success is True


# ===========================================================================
# _cancel_reboot
# ===========================================================================

class TestCancelReboot:
    def test_cancel_reboot_calls_manager(self):
        mock_manager = MagicMock()
        mock_manager.cancel_pending_reboot = AsyncMock()
        with patch("elle.daemon.reboot.get_manager", return_value=mock_manager), \
             patch("asyncio.create_task") as mock_task:
            _cancel_reboot("int-1")
            mock_task.assert_called_once()

    def test_cancel_reboot_import_error(self):
        with patch.dict("sys.modules", {"elle.daemon.reboot": None}):
            # Should not raise (ImportError handled)
            _cancel_reboot("int-1")

    def test_cancel_reboot_general_error(self):
        with patch("elle.daemon.reboot.get_manager", side_effect=RuntimeError("oops")):
            # Should not raise
            _cancel_reboot("int-1")


# ===========================================================================
# _push_to_mobile
# ===========================================================================

class TestPushToMobile:
    def test_push_when_available(self):
        mock_notifier = MagicMock()
        mock_notifier.is_available.return_value = True
        mock_notifier.push_notification = AsyncMock()
        with patch("elle.daemon.notifications.mobile_push.get_mobile_notifier", return_value=mock_notifier), \
             patch("asyncio.create_task") as mock_task:
            n = _make_notification()
            _push_to_mobile(n)
            mock_task.assert_called_once()

    def test_push_when_not_available(self):
        mock_notifier = MagicMock()
        mock_notifier.is_available.return_value = False
        with patch("elle.daemon.notifications.mobile_push.get_mobile_notifier", return_value=mock_notifier), \
             patch("asyncio.create_task") as mock_task:
            n = _make_notification()
            _push_to_mobile(n)
            mock_task.assert_not_called()

    def test_push_import_error(self):
        with patch.dict("sys.modules", {"elle.daemon.notifications.mobile_push": None}):
            n = _make_notification()
            # Should not raise
            _push_to_mobile(n)


# ===========================================================================
# NotificationService
# ===========================================================================

class TestNotificationService:
    def test_get_service_singleton(self):
        with patch("elle.daemon.notifications.service._service", None):
            svc1 = get_service()
            assert isinstance(svc1, NotificationService)

    def test_queue_notification(self):
        svc = NotificationService()
        n = _make_notification()
        svc.queue(n)
        assert not svc._queue.empty()

    def test_add_to_history(self):
        svc = NotificationService()
        n = _make_notification()
        result = NotificationResult(success=True, method="test")
        svc._add_to_history(n, result)
        assert len(svc._history) == 1

    def test_history_max_limit(self):
        svc = NotificationService()
        svc._max_history = 5
        for i in range(10):
            n = _make_notification(title=f"Test {i}")
            result = NotificationResult(success=True, method="test")
            svc._add_to_history(n, result)
        assert len(svc._history) == 5

    def test_get_history(self):
        svc = NotificationService()
        for i in range(5):
            n = _make_notification(title=f"Test {i}")
            result = NotificationResult(success=True, method="test")
            svc._add_to_history(n, result)
        history = svc.get_history(limit=3)
        assert len(history) == 3

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        svc = NotificationService()
        await svc.start()
        assert svc._running is True
        assert svc._task is not None
        await svc.stop()
        assert svc._running is False

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        svc = NotificationService()
        await svc.start()
        task1 = svc._task
        await svc.start()  # Should be a no-op
        assert svc._task is task1
        await svc.stop()


# ===========================================================================
# _create_action_callback
# ===========================================================================

class TestCreateActionCallback:
    def test_custom_command_action(self):
        action = NotificationAction(id="custom", label="Run", command="echo hello")
        n = _make_notification()
        callback = _create_action_callback(action, n)
        with patch("subprocess.Popen") as mock_popen:
            callback(None, "custom", None)
            mock_popen.assert_called_once()

    def test_custom_command_exception(self):
        action = NotificationAction(id="custom", label="Run", command="echo hello")
        n = _make_notification()
        callback = _create_action_callback(action, n)
        with patch("subprocess.Popen", side_effect=OSError("fail")):
            # Should not raise
            callback(None, "custom", None)

    def test_open_action_callback(self):
        action = NotificationAction(id="open", label="Open")
        n = _make_notification(context_type="incident", context_id="inc-1")
        callback = _create_action_callback(action, n)
        with patch("elle.daemon.notifications.service._open_elle_repl") as mock_open:
            callback(None, "open", None)
            mock_open.assert_called_once_with("incident", "inc-1")

    def test_cancel_action_callback(self):
        action = NotificationAction(id="cancel", label="Cancel")
        n = _make_notification(context_type="reboot", context_id="int-1")
        callback = _create_action_callback(action, n)
        with patch("elle.daemon.notifications.service._cancel_reboot") as mock_cancel:
            callback(None, "cancel", None)
            mock_cancel.assert_called_once_with("int-1")

    def test_dismiss_action_callback(self):
        action = NotificationAction(id="dismiss", label="Dismiss")
        n = _make_notification()
        callback = _create_action_callback(action, n)
        # Should not raise
        callback(None, "dismiss", None)

    def test_execute_plan_action_callback(self):
        action = NotificationAction(id="execute_plan", label="Execute")
        n = _make_notification(context_id="inc-1")
        callback = _create_action_callback(action, n)
        with patch("elle.daemon.notifications.service._execute_forecast_plan") as mock_exec:
            callback(None, "execute_plan", None)
            mock_exec.assert_called_once_with("inc-1")

    def test_confirm_action_callback(self):
        action = NotificationAction(id="confirm", label="Confirm")
        n = _make_notification(context_type="incident", context_id="inc-1")
        callback = _create_action_callback(action, n)
        with patch("elle.daemon.notifications.service._open_elle_repl") as mock_open:
            callback(None, "confirm", None)
            mock_open.assert_called_once()
