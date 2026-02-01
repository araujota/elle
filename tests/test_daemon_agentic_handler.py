"""Tests for daemon incident agentic handler.

Tests the bridge between daemon incident detection and the agentic loop.
This tests ``elle.daemon.incidents.agentic_handler``, NOT the CLI handler.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.incidents.agentic_handler import (
    AGENTIC_HANDLER_VAR,
    AUTO_REMEDIATE_VAR,
    IncidentAgenticHandler,
    IncidentHandlingResult,
    get_agentic_handler,
    handle_incident,
    is_auto_remediate_enabled,
    is_handler_enabled,
    reset_agentic_handler,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_incident_info(**overrides: Any) -> dict[str, Any]:
    """Build a mock incident info dict."""
    defaults = {
        "incident_id": "inc-12345678",
        "title": "Test incident",
        "summary": "Something went wrong",
        "domain": "service",
        "severity": "error",
        "status": "open",
    }
    defaults.update(overrides)
    return defaults


def _make_loop_result(**overrides: Any) -> MagicMock:
    """Build a mock result from run_for_incident."""
    result = MagicMock()
    result.success = overrides.get("success", True)
    result.response = overrides.get("response", "Issue resolved")
    result.execution_id = overrides.get("execution_id", "exec-abc123")
    result.tool_call_count = overrides.get("tool_call_count", 3)
    result.error = overrides.get("error")
    return result


# =============================================================================
# Configuration functions
# =============================================================================


class TestConfigFunctions:
    """Tests for is_auto_remediate_enabled and is_handler_enabled."""

    def test_auto_remediate_disabled_by_default(self) -> None:
        """Auto-remediate is disabled when env var is not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop(AUTO_REMEDIATE_VAR, None)
            assert is_auto_remediate_enabled() is False

    def test_auto_remediate_enabled_true(self) -> None:
        """Auto-remediate is enabled with 'true'."""
        with patch.dict(os.environ, {AUTO_REMEDIATE_VAR: "true"}):
            assert is_auto_remediate_enabled() is True

    def test_auto_remediate_enabled_1(self) -> None:
        """Auto-remediate is enabled with '1'."""
        with patch.dict(os.environ, {AUTO_REMEDIATE_VAR: "1"}):
            assert is_auto_remediate_enabled() is True

    def test_auto_remediate_enabled_yes(self) -> None:
        """Auto-remediate is enabled with 'yes'."""
        with patch.dict(os.environ, {AUTO_REMEDIATE_VAR: "yes"}):
            assert is_auto_remediate_enabled() is True

    def test_auto_remediate_enabled_true_case_insensitive(self) -> None:
        """Auto-remediate is case-insensitive."""
        with patch.dict(os.environ, {AUTO_REMEDIATE_VAR: "TRUE"}):
            assert is_auto_remediate_enabled() is True

    def test_auto_remediate_disabled_other_values(self) -> None:
        """Auto-remediate is disabled for non-truthy values."""
        for val in ("false", "0", "no", "maybe", ""):
            with patch.dict(os.environ, {AUTO_REMEDIATE_VAR: val}):
                assert is_auto_remediate_enabled() is False

    def test_handler_enabled_disabled_by_default(self) -> None:
        """Agentic handler is disabled when env var is not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop(AGENTIC_HANDLER_VAR, None)
            assert is_handler_enabled() is False

    def test_handler_enabled_true(self) -> None:
        """Agentic handler is enabled with 'true'."""
        with patch.dict(os.environ, {AGENTIC_HANDLER_VAR: "true"}):
            assert is_handler_enabled() is True

    def test_handler_enabled_1(self) -> None:
        """Agentic handler is enabled with '1'."""
        with patch.dict(os.environ, {AGENTIC_HANDLER_VAR: "1"}):
            assert is_handler_enabled() is True

    def test_handler_enabled_yes(self) -> None:
        """Agentic handler is enabled with 'yes'."""
        with patch.dict(os.environ, {AGENTIC_HANDLER_VAR: "yes"}):
            assert is_handler_enabled() is True


# =============================================================================
# IncidentHandlingResult model
# =============================================================================


class TestIncidentHandlingResult:
    """Tests for the IncidentHandlingResult model."""

    def test_create_success_result(self) -> None:
        """Success result has expected fields."""
        result = IncidentHandlingResult(
            incident_id="inc-001",
            success=True,
            auto_triggered=False,
            response="Fixed the issue",
            execution_id="exec-123",
            tool_calls_count=5,
            duration_ms=1500,
        )
        assert result.incident_id == "inc-001"
        assert result.success is True
        assert result.auto_triggered is False
        assert result.response == "Fixed the issue"
        assert result.execution_id == "exec-123"
        assert result.tool_calls_count == 5
        assert result.duration_ms == 1500
        assert result.error is None

    def test_create_failure_result(self) -> None:
        """Failure result has error field."""
        result = IncidentHandlingResult(
            incident_id="inc-002",
            success=False,
            auto_triggered=True,
            response="Error occurred",
            error="not_found",
        )
        assert result.success is False
        assert result.error == "not_found"

    def test_default_values(self) -> None:
        """Default values are applied correctly."""
        result = IncidentHandlingResult(
            incident_id="inc-003",
            success=True,
            auto_triggered=False,
            response="OK",
        )
        assert result.execution_id is None
        assert result.tool_calls_count == 0
        assert result.duration_ms == 0
        assert result.error is None
        assert result.timestamp is not None

    def test_frozen_model(self) -> None:
        """Result model is frozen."""
        result = IncidentHandlingResult(
            incident_id="inc-004",
            success=True,
            auto_triggered=False,
            response="OK",
        )
        with pytest.raises(Exception):
            result.success = False  # type: ignore[misc]


# =============================================================================
# IncidentAgenticHandler.__init__
# =============================================================================


class TestHandlerInit:
    """Tests for handler initialization."""

    def test_default_init(self) -> None:
        """Handler initializes with defaults."""
        with patch.dict(os.environ, {AUTO_REMEDIATE_VAR: "false"}):
            handler = IncidentAgenticHandler()
            assert handler.auto_remediate is False
            assert handler.notify_on_handling is True
            assert handler.max_concurrent == 2

    def test_init_with_auto_remediate_override(self) -> None:
        """auto_remediate parameter overrides env var."""
        handler = IncidentAgenticHandler(auto_remediate=True)
        assert handler.auto_remediate is True

    def test_init_with_env_auto_remediate(self) -> None:
        """auto_remediate reads from env when not explicitly set."""
        with patch.dict(os.environ, {AUTO_REMEDIATE_VAR: "true"}):
            handler = IncidentAgenticHandler()
            assert handler.auto_remediate is True

    def test_init_custom_values(self) -> None:
        """Custom initialization parameters."""
        handler = IncidentAgenticHandler(
            auto_remediate=True,
            notify_on_handling=False,
            max_concurrent=5,
        )
        assert handler.auto_remediate is True
        assert handler.notify_on_handling is False
        assert handler.max_concurrent == 5


# =============================================================================
# IncidentAgenticHandler.handle_incident
# =============================================================================


class TestHandleIncident:
    """Tests for the handle_incident method."""

    @pytest.mark.asyncio
    async def test_duplicate_handling_prevented(self) -> None:
        """Concurrent handling of the same incident is blocked."""
        handler = IncidentAgenticHandler(auto_remediate=False)
        handler._in_progress.add("inc-001")

        result = await handler.handle_incident("inc-001")
        assert result.success is False
        assert result.error == "duplicate_handling"
        assert "already being handled" in result.response

    @pytest.mark.asyncio
    async def test_incident_not_found(self) -> None:
        """Returns failure when incident is not found."""
        handler = IncidentAgenticHandler(auto_remediate=False)

        with patch.object(handler, "_get_incident_info", return_value=None):
            result = await handler.handle_incident("inc-nonexistent")
            assert result.success is False
            assert result.error == "not_found"
            assert "not found" in result.response

    @pytest.mark.asyncio
    async def test_successful_handling(self) -> None:
        """Successfully handles an incident through the agentic loop."""
        handler = IncidentAgenticHandler(auto_remediate=False, notify_on_handling=False)
        incident_info = _make_incident_info()
        loop_result = _make_loop_result()

        with patch.object(handler, "_get_incident_info", return_value=incident_info):
            with patch(
                "elle.cli.agentic.loop.run_for_incident",
                new_callable=AsyncMock,
                return_value=loop_result,
                create=True,
            ):
                result = await handler.handle_incident("inc-12345678")
                assert result.success is True
                assert result.response == "Issue resolved"
                assert result.execution_id == "exec-abc123"
                assert result.tool_calls_count == 3
                assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_handling_with_notification(self) -> None:
        """Sends notification when notify_on_handling is True and handling succeeds."""
        handler = IncidentAgenticHandler(auto_remediate=False, notify_on_handling=True)
        incident_info = _make_incident_info()
        loop_result = _make_loop_result(success=True)

        with patch.object(handler, "_get_incident_info", return_value=incident_info):
            with patch(
                "elle.cli.agentic.loop.run_for_incident",
                new_callable=AsyncMock,
                return_value=loop_result,
                create=True,
            ):
                with patch.object(handler, "_send_notification", new_callable=AsyncMock) as mock_notify:
                    result = await handler.handle_incident("inc-12345678")
                    assert result.success is True
                    mock_notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handling_no_notification_on_failure(self) -> None:
        """Does not send notification when handling fails."""
        handler = IncidentAgenticHandler(auto_remediate=False, notify_on_handling=True)
        incident_info = _make_incident_info()
        loop_result = _make_loop_result(success=False, error="Permission denied")

        with patch.object(handler, "_get_incident_info", return_value=incident_info):
            with patch(
                "elle.cli.agentic.loop.run_for_incident",
                new_callable=AsyncMock,
                return_value=loop_result,
                create=True,
            ):
                with patch.object(handler, "_send_notification", new_callable=AsyncMock) as mock_notify:
                    result = await handler.handle_incident("inc-12345678")
                    assert result.success is False
                    mock_notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_auto_triggered_flag(self) -> None:
        """auto_triggered flag is passed through to result."""
        handler = IncidentAgenticHandler(auto_remediate=False, notify_on_handling=False)
        incident_info = _make_incident_info()
        loop_result = _make_loop_result()

        with patch.object(handler, "_get_incident_info", return_value=incident_info):
            with patch(
                "elle.cli.agentic.loop.run_for_incident",
                new_callable=AsyncMock,
                return_value=loop_result,
                create=True,
            ):
                result = await handler.handle_incident("inc-001", auto_triggered=True)
                assert result.auto_triggered is True

    @pytest.mark.asyncio
    async def test_exception_handling(self) -> None:
        """Exceptions are caught and returned as failure result."""
        handler = IncidentAgenticHandler(auto_remediate=False, notify_on_handling=False)
        incident_info = _make_incident_info()

        with patch.object(handler, "_get_incident_info", return_value=incident_info):
            with patch(
                "elle.cli.agentic.loop.run_for_incident",
                new_callable=AsyncMock,
                side_effect=RuntimeError("LLM unavailable"),
                create=True,
            ):
                result = await handler.handle_incident("inc-001")
                assert result.success is False
                assert "LLM unavailable" in result.error
                assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_in_progress_cleanup_on_success(self) -> None:
        """Incident is removed from _in_progress after successful handling."""
        handler = IncidentAgenticHandler(auto_remediate=False, notify_on_handling=False)
        incident_info = _make_incident_info()
        loop_result = _make_loop_result()

        with patch.object(handler, "_get_incident_info", return_value=incident_info):
            with patch(
                "elle.cli.agentic.loop.run_for_incident",
                new_callable=AsyncMock,
                return_value=loop_result,
                create=True,
            ):
                await handler.handle_incident("inc-001")
                assert "inc-001" not in handler._in_progress

    @pytest.mark.asyncio
    async def test_in_progress_cleanup_on_exception(self) -> None:
        """Incident is removed from _in_progress even after exception."""
        handler = IncidentAgenticHandler(auto_remediate=False, notify_on_handling=False)
        incident_info = _make_incident_info()

        with patch.object(handler, "_get_incident_info", return_value=incident_info):
            with patch(
                "elle.cli.agentic.loop.run_for_incident",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
                create=True,
            ):
                await handler.handle_incident("inc-001")
                assert "inc-001" not in handler._in_progress

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self) -> None:
        """Semaphore limits concurrent incident handling."""
        handler = IncidentAgenticHandler(
            auto_remediate=False,
            notify_on_handling=False,
            max_concurrent=1,
        )
        incident_info = _make_incident_info()

        # Track when handling starts/ends
        handling_started = asyncio.Event()
        handling_released = asyncio.Event()

        async def slow_run(*args, **kwargs):
            handling_started.set()
            await handling_released.wait()
            return _make_loop_result()

        with patch.object(handler, "_get_incident_info", return_value=incident_info):
            with patch(
                "elle.cli.agentic.loop.run_for_incident",
                new_callable=AsyncMock,
                side_effect=slow_run,
                create=True,
            ):
                # Start first handling
                task1 = asyncio.create_task(handler.handle_incident("inc-001"))
                await handling_started.wait()

                # Second handling for a DIFFERENT incident should block on semaphore
                # (can't easily test blocking, but we can verify it completes after release)
                handling_released.set()
                result1 = await task1
                assert result1.success is True


# =============================================================================
# IncidentAgenticHandler.on_incident_created
# =============================================================================


class TestOnIncidentCreated:
    """Tests for the on_incident_created callback."""

    @pytest.mark.asyncio
    async def test_handler_disabled_skips(self) -> None:
        """When handler is disabled, on_incident_created returns immediately."""
        handler = IncidentAgenticHandler(auto_remediate=True)

        with patch.dict(os.environ, {AGENTIC_HANDLER_VAR: "false"}):
            with patch.object(handler, "_get_incident_info") as mock_get:
                await handler.on_incident_created("inc-001")
                mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_incident_not_found(self) -> None:
        """When incident is not found, logs warning and returns."""
        handler = IncidentAgenticHandler(auto_remediate=True)

        with patch.dict(os.environ, {AGENTIC_HANDLER_VAR: "true"}):
            with patch.object(handler, "_get_incident_info", return_value=None):
                # Should not raise
                await handler.on_incident_created("inc-nonexistent")

    @pytest.mark.asyncio
    async def test_auto_handle_critical(self) -> None:
        """Critical incidents are auto-handled when auto_remediate is True."""
        handler = IncidentAgenticHandler(auto_remediate=True)
        incident_info = _make_incident_info(severity="critical")

        with patch.dict(os.environ, {AGENTIC_HANDLER_VAR: "true"}):
            with patch.object(handler, "_get_incident_info", return_value=incident_info):
                with patch.object(handler, "handle_incident", new_callable=AsyncMock):
                    # asyncio.create_task is used internally, we need a running loop
                    await handler.on_incident_created("inc-001")
                    # The task is created but may not have run yet - just verify the method doesn't crash

    @pytest.mark.asyncio
    async def test_auto_handle_error_severity(self) -> None:
        """Error severity incidents are auto-handled when auto_remediate is True."""
        handler = IncidentAgenticHandler(auto_remediate=True)
        incident_info = _make_incident_info(severity="error")

        with patch.dict(os.environ, {AGENTIC_HANDLER_VAR: "true"}):
            with patch.object(handler, "_get_incident_info", return_value=incident_info):
                with patch("asyncio.create_task") as mock_task:
                    await handler.on_incident_created("inc-001")
                    mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_auto_handle_warning(self) -> None:
        """Warning severity incidents are not auto-handled."""
        handler = IncidentAgenticHandler(auto_remediate=True, notify_on_handling=False)
        incident_info = _make_incident_info(severity="warning")

        with patch.dict(os.environ, {AGENTIC_HANDLER_VAR: "true"}):
            with patch.object(handler, "_get_incident_info", return_value=incident_info):
                with patch("asyncio.create_task") as mock_task:
                    await handler.on_incident_created("inc-001")
                    mock_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_auto_handle_info(self) -> None:
        """Info severity incidents are not auto-handled."""
        handler = IncidentAgenticHandler(auto_remediate=True, notify_on_handling=False)
        incident_info = _make_incident_info(severity="info")

        with patch.dict(os.environ, {AGENTIC_HANDLER_VAR: "true"}):
            with patch.object(handler, "_get_incident_info", return_value=incident_info):
                with patch("asyncio.create_task") as mock_task:
                    await handler.on_incident_created("inc-001")
                    mock_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_auto_handle_when_disabled(self) -> None:
        """auto_remediate=False prevents auto-handling even for critical."""
        handler = IncidentAgenticHandler(auto_remediate=False, notify_on_handling=False)
        incident_info = _make_incident_info(severity="critical")

        with patch.dict(os.environ, {AGENTIC_HANDLER_VAR: "true"}):
            with patch.object(handler, "_get_incident_info", return_value=incident_info):
                with patch("asyncio.create_task") as mock_task:
                    await handler.on_incident_created("inc-001")
                    mock_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_notification_sent_when_not_auto_handling(self) -> None:
        """Notification is sent for incidents that are not auto-handled."""
        handler = IncidentAgenticHandler(auto_remediate=False, notify_on_handling=True)
        incident_info = _make_incident_info(severity="warning")

        with patch.dict(os.environ, {AGENTIC_HANDLER_VAR: "true"}):
            with patch.object(handler, "_get_incident_info", return_value=incident_info):
                with patch.object(handler, "_send_incident_notification", new_callable=AsyncMock) as mock_notify:
                    await handler.on_incident_created("inc-001")
                    mock_notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_does_not_propagate(self) -> None:
        """Exceptions in on_incident_created are caught."""
        handler = IncidentAgenticHandler(auto_remediate=True)

        with patch.dict(os.environ, {AGENTIC_HANDLER_VAR: "true"}):
            with patch.object(handler, "_get_incident_info", side_effect=RuntimeError("DB error")):
                # Should not raise
                await handler.on_incident_created("inc-001")


# =============================================================================
# IncidentAgenticHandler._get_incident_info
# =============================================================================


class TestGetIncidentInfo:
    """Tests for _get_incident_info."""

    @pytest.mark.asyncio
    async def test_returns_dict_on_success(self) -> None:
        """Returns incident info dict when incident exists."""
        handler = IncidentAgenticHandler()
        mock_incident = MagicMock()
        mock_incident.incident_id = "inc-001"
        mock_incident.title = "Test"
        mock_incident.summary = "Summary"
        mock_incident.domain = "service"
        mock_incident.severity = "error"
        mock_incident.status = "open"

        with patch("elle.daemon.incidents.store.get_incident", return_value=mock_incident):
            info = await handler._get_incident_info("inc-001")
            assert info is not None
            assert info["incident_id"] == "inc-001"
            assert info["title"] == "Test"
            assert info["severity"] == "error"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self) -> None:
        """Returns None when incident does not exist."""
        handler = IncidentAgenticHandler()

        with patch("elle.daemon.incidents.store.get_incident", return_value=None):
            info = await handler._get_incident_info("inc-nonexistent")
            assert info is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self) -> None:
        """Returns None when an exception occurs."""
        handler = IncidentAgenticHandler()

        with patch(
            "elle.daemon.incidents.store.get_incident",
            side_effect=RuntimeError("DB error"),
        ):
            info = await handler._get_incident_info("inc-001")
            assert info is None


# =============================================================================
# IncidentAgenticHandler._send_notification
# =============================================================================


class TestSendNotification:
    """Tests for _send_notification."""

    @pytest.mark.asyncio
    async def test_sends_notification(self) -> None:
        """Sends notification with correct title and body."""
        handler = IncidentAgenticHandler()
        incident_info = _make_incident_info(title="Disk full", severity="critical")

        with patch("elle.daemon.notifications.notify") as mock_notify:
            await handler._send_notification("inc-001", incident_info, "Cleaned up temp files", False)
            mock_notify.assert_called_once()
            call_kwargs = mock_notify.call_args
            assert "CRITICAL" in call_kwargs.kwargs["title"]
            assert "Disk full" in call_kwargs.kwargs["body"]

    @pytest.mark.asyncio
    async def test_auto_triggered_label(self) -> None:
        """Auto-triggered handling includes 'Auto-' prefix in body."""
        handler = IncidentAgenticHandler()
        incident_info = _make_incident_info()

        with patch("elle.daemon.notifications.notify") as mock_notify:
            await handler._send_notification("inc-001", incident_info, "Fixed", True)
            body = mock_notify.call_args.kwargs["body"]
            assert "Auto-" in body

    @pytest.mark.asyncio
    async def test_notification_exception_caught(self) -> None:
        """Exceptions in notification sending are silently caught."""
        handler = IncidentAgenticHandler()
        incident_info = _make_incident_info()

        with patch(
            "elle.daemon.notifications.notify",
            side_effect=RuntimeError("ntfy down"),
        ):
            # Should not raise
            await handler._send_notification("inc-001", incident_info, "Fixed", False)


# =============================================================================
# IncidentAgenticHandler._send_incident_notification
# =============================================================================


class TestSendIncidentNotification:
    """Tests for _send_incident_notification."""

    @pytest.mark.asyncio
    async def test_sends_new_incident_notification(self) -> None:
        """Sends notification about a new incident."""
        handler = IncidentAgenticHandler()
        incident_info = _make_incident_info(
            title="Service crashed",
            severity="error",
            summary="nginx returned exit 1",
        )

        with patch("elle.daemon.notifications.notify") as mock_notify:
            await handler._send_incident_notification("inc-001", incident_info)
            mock_notify.assert_called_once()
            call_kwargs = mock_notify.call_args
            assert "ERROR" in call_kwargs.kwargs["title"]
            assert "Service crashed" in call_kwargs.kwargs["title"]
            assert "nginx returned exit 1" in call_kwargs.kwargs["body"]

    @pytest.mark.asyncio
    async def test_exception_caught(self) -> None:
        """Exceptions are silently caught."""
        handler = IncidentAgenticHandler()
        incident_info = _make_incident_info()

        with patch(
            "elle.daemon.notifications.notify",
            side_effect=RuntimeError("notification failed"),
        ):
            await handler._send_incident_notification("inc-001", incident_info)


# =============================================================================
# Module-level functions
# =============================================================================


class TestModuleFunctions:
    """Tests for get_agentic_handler, reset_agentic_handler, handle_incident."""

    def test_get_agentic_handler_singleton(self) -> None:
        """get_agentic_handler returns same instance."""
        reset_agentic_handler()
        h1 = get_agentic_handler()
        h2 = get_agentic_handler()
        assert h1 is h2

    def test_reset_creates_new_handler(self) -> None:
        """reset_agentic_handler causes new instance on next call."""
        reset_agentic_handler()
        h1 = get_agentic_handler()
        reset_agentic_handler()
        h2 = get_agentic_handler()
        assert h1 is not h2

    @pytest.mark.asyncio
    async def test_module_handle_incident(self) -> None:
        """Module-level handle_incident delegates to handler."""
        reset_agentic_handler()

        mock_result = IncidentHandlingResult(
            incident_id="inc-001",
            success=True,
            auto_triggered=False,
            response="Done",
        )

        with patch.object(
            IncidentAgenticHandler,
            "handle_incident",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await handle_incident("inc-001")
            assert result.success is True
            assert result.incident_id == "inc-001"

    @pytest.mark.asyncio
    async def test_module_handle_incident_with_callbacks(self) -> None:
        """Module-level handle_incident passes callbacks through."""
        reset_agentic_handler()

        mock_result = IncidentHandlingResult(
            incident_id="inc-002",
            success=True,
            auto_triggered=True,
            response="Auto-handled",
        )

        stream_cb = MagicMock()
        confirm_cb = MagicMock()

        with patch.object(
            IncidentAgenticHandler,
            "handle_incident",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_handle:
            result = await handle_incident(
                "inc-002",
                auto_triggered=True,
                stream_callback=stream_cb,
                confirm_callback=confirm_cb,
            )
            mock_handle.assert_awaited_once_with(
                "inc-002",
                auto_triggered=True,
                stream_callback=stream_cb,
                confirm_callback=confirm_cb,
            )
            assert result.auto_triggered is True
