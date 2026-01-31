from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.config import ApiConfig, Config, QueueConfig
from elle.daemon.main import (
    ElledDaemon,
    setup_logging,
)
from elle.daemon.telemetry.models import DaemonStatus, TelemetryEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _test_config(tmp_path: Path) -> Config:
    """Create a Config suitable for testing."""
    return Config(
        api=ApiConfig(enabled=False),
        queues=QueueConfig(raw_queue_size=100, event_queue_size=50),
        capability_versioning_enabled=False,
        capability_bootstrap_enabled=False,
        auto_learn_new_packages=False,
    )


def _mock_event(**kw: Any) -> TelemetryEvent:
    return TelemetryEvent(
        source="journal",
        severity=kw.get("severity", "warning"),
        category=kw.get("category", "disk"),
        message=kw.get("message", "Test event"),
        entity=kw.get("entity"),
        raw=kw.get("raw", {}),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg(tmp_path):
    return _test_config(tmp_path)


@pytest.fixture
def daemon(cfg):
    return ElledDaemon(config=cfg)


# ---------------------------------------------------------------------------
# ElledDaemon: init / properties
# ---------------------------------------------------------------------------


class TestDaemonInit:
    def test_default_state(self, daemon):
        assert daemon.started_at is None
        assert daemon.event_queue is None
        assert daemon._telemetryd_watcher is None
        assert daemon._events_total == 0
        assert daemon._incidents_total == 0

    def test_uptime_not_started(self, daemon):
        assert daemon.uptime_sec == 0

    def test_uptime_started(self, daemon):
        daemon.started_at = datetime.now(timezone.utc)
        assert daemon.uptime_sec >= 0

    def test_config_set(self, daemon, cfg):
        assert daemon.config is cfg

    def test_default_config_used(self):
        with patch("elle.daemon.main.get_config") as mock_gc:
            mock_gc.return_value = Config()
            d = ElledDaemon()
            assert d.config is mock_gc.return_value


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_not_started(self, daemon):
        status = daemon.get_status()
        assert isinstance(status, DaemonStatus)
        assert status.events_total == 0
        assert status.pid == os.getpid()

    def test_started_with_queue(self, daemon):
        daemon.started_at = datetime.now(timezone.utc)
        daemon._events_total = 42
        from elle.daemon.telemetry.queue import TelemetryQueue

        daemon.event_queue = TelemetryQueue(maxsize=100, name="events")
        status = daemon.get_status()
        assert status.events_total == 42

    def test_watcher_not_running_error(self, daemon):
        mock_watcher = MagicMock()
        mock_watcher.running = False
        daemon._telemetryd_watcher = mock_watcher
        status = daemon.get_status()
        assert len(status.errors) == 1
        assert "Telemetryd" in status.errors[0]
        assert status.healthy is False

    def test_watcher_running_no_errors(self, daemon):
        mock_watcher = MagicMock()
        mock_watcher.running = True
        daemon._telemetryd_watcher = mock_watcher
        status = daemon.get_status()
        assert status.healthy is True


# ---------------------------------------------------------------------------
# _init_database
# ---------------------------------------------------------------------------


class TestInitDatabase:
    @patch("elle.common.db.init_all_schemas")
    @patch("elle.storage.engine.configure_pool")
    def test_init_calls_schemas(self, mock_pool, mock_schemas, daemon):
        daemon._init_database()
        mock_schemas.assert_called_once()

    @patch("elle.common.db.init_all_schemas", side_effect=RuntimeError("DB error"))
    @patch("elle.storage.engine.configure_pool")
    def test_init_db_error_logged(self, mock_pool, mock_init, daemon, caplog):
        with caplog.at_level(logging.ERROR):
            daemon._init_database()
        assert "Failed to initialize database" in caplog.text


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


class TestStart:
    @patch("elle.daemon.main.is_telemetryd_available", return_value=False)
    async def test_start_no_telemetryd(self, _, daemon):
        with pytest.raises(RuntimeError, match="telemetryd"):
            await daemon.start()

    @patch("elle.daemon.main.is_telemetryd_available", return_value=True)
    async def test_start_initializes_components(self, _, daemon, tmp_path):
        # Mock out all the sub-components
        mock_token_mgr = MagicMock()
        mock_token_mgr.initialize = MagicMock()
        mock_token_mgr.token_path = tmp_path / "token"

        mock_state_cache = MagicMock()
        mock_state_cache.start = AsyncMock()

        mock_watcher = MagicMock()
        mock_watcher.start = AsyncMock()

        with patch("elle.daemon.api.session_token.get_token_manager", return_value=mock_token_mgr):
            with patch.object(daemon, "_init_database"):
                with patch.object(daemon, "_warmup_models", new_callable=AsyncMock):
                    with patch.object(daemon, "_start_notification_service", new_callable=AsyncMock):
                        with patch.object(daemon, "_check_reboot_recovery", new_callable=AsyncMock):
                            with patch("elle.daemon.main.TelemetrydWatcher", return_value=mock_watcher):
                                with patch("elle.daemon.main.StateCache", return_value=mock_state_cache):
                                    with patch("elle.daemon.main.create_queues") as mock_cq:
                                        mock_cq.return_value = (MagicMock(), MagicMock())
                                        with patch.object(daemon, "_processor_loop", new_callable=AsyncMock):
                                            # Prevent manvault import error
                                            with patch.dict(
                                                "sys.modules", {"elle.daemon.manvault.service": MagicMock()}
                                            ):
                                                await daemon.start()

        assert daemon.started_at is not None
        assert daemon.event_queue is not None
        assert len(daemon._tasks) >= 1


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


class TestStop:
    async def test_stop_sets_shutdown(self, daemon):
        daemon.started_at = datetime.now(timezone.utc)
        await daemon.stop()
        assert daemon.shutdown.is_set()

    async def test_stop_cancels_tasks(self, daemon):
        daemon.started_at = datetime.now(timezone.utc)

        async def _long_running():
            await asyncio.sleep(3600)

        task = asyncio.create_task(_long_running())
        daemon._tasks = [task]
        await daemon.stop()
        assert task.cancelled()

    async def test_stop_cleans_session_token(self, daemon):
        daemon.started_at = datetime.now(timezone.utc)
        mock_mgr = MagicMock()
        daemon._session_token_manager = mock_mgr
        await daemon.stop()
        mock_mgr.cleanup.assert_called_once()

    async def test_stop_stops_watcher(self, daemon):
        daemon.started_at = datetime.now(timezone.utc)
        mock_watcher = MagicMock()
        mock_watcher.stop = AsyncMock()
        daemon._telemetryd_watcher = mock_watcher
        await daemon.stop()
        mock_watcher.stop.assert_awaited_once()

    async def test_stop_stops_state_cache(self, daemon):
        daemon.started_at = datetime.now(timezone.utc)
        mock_cache = MagicMock()
        mock_cache.stop = AsyncMock()
        daemon._state_cache = mock_cache
        await daemon.stop()
        mock_cache.stop.assert_awaited_once()

    async def test_stop_stops_manvault(self, daemon):
        daemon.started_at = datetime.now(timezone.utc)
        mock_mv = MagicMock()
        mock_mv.stop = AsyncMock()
        daemon._manvault_service = mock_mv
        await daemon.stop()
        mock_mv.stop.assert_awaited_once()

    async def test_stop_stops_notification(self, daemon):
        daemon.started_at = datetime.now(timezone.utc)
        mock_svc = MagicMock()
        mock_svc.stop = AsyncMock()
        daemon._notification_service = mock_svc
        await daemon.stop()
        mock_svc.stop.assert_awaited_once()


# ---------------------------------------------------------------------------
# _warmup_models
# ---------------------------------------------------------------------------


class TestWarmupModels:
    async def test_import_error(self, daemon):
        with patch.dict("sys.modules", {"elle.rag.model_warmup": None}):
            await daemon._warmup_models()
        # Should not raise

    async def test_exception(self, daemon, caplog):
        with patch.dict("sys.modules", {"elle.rag.model_warmup": MagicMock()}):
            with patch("elle.rag.model_warmup.ModelWarmupService", side_effect=RuntimeError("fail")):
                await daemon._warmup_models()


# ---------------------------------------------------------------------------
# _stop_warmup_service
# ---------------------------------------------------------------------------


class TestStopWarmupService:
    async def test_no_service(self, daemon):
        daemon._warmup_service = None
        await daemon._stop_warmup_service()

    async def test_close_called(self, daemon):
        mock_svc = MagicMock()
        mock_svc.close = AsyncMock()
        daemon._warmup_service = mock_svc
        await daemon._stop_warmup_service()
        mock_svc.close.assert_awaited_once()

    async def test_close_error(self, daemon):
        mock_svc = MagicMock()
        mock_svc.close = AsyncMock(side_effect=RuntimeError("fail"))
        daemon._warmup_service = mock_svc
        await daemon._stop_warmup_service()  # Should not raise


# ---------------------------------------------------------------------------
# _start_notification_service
# ---------------------------------------------------------------------------


class TestStartNotificationService:
    async def test_import_error(self, daemon):
        with patch.dict("sys.modules", {"elle.daemon.notifications": None}):
            await daemon._start_notification_service()

    async def test_exception(self, daemon):
        mock_mod = MagicMock()
        mock_svc = MagicMock()
        mock_svc.start = AsyncMock(side_effect=RuntimeError("fail"))
        mock_mod.get_service.return_value = mock_svc
        with patch.dict("sys.modules", {"elle.daemon.notifications": mock_mod}):
            await daemon._start_notification_service()


# ---------------------------------------------------------------------------
# _stop_notification_service
# ---------------------------------------------------------------------------


class TestStopNotificationService:
    async def test_no_service(self, daemon):
        daemon._notification_service = None
        await daemon._stop_notification_service()

    async def test_stop_error(self, daemon):
        mock_svc = MagicMock()
        mock_svc.stop = AsyncMock(side_effect=RuntimeError("fail"))
        daemon._notification_service = mock_svc
        await daemon._stop_notification_service()


# ---------------------------------------------------------------------------
# _check_reboot_recovery
# ---------------------------------------------------------------------------


class TestCheckRebootRecovery:
    async def test_import_error(self, daemon):
        with patch.dict("sys.modules", {"elle.daemon.reboot": None}):
            await daemon._check_reboot_recovery()

    async def test_no_pending_intent(self, daemon):
        mock_mgr = MagicMock()
        mock_mgr.check_pending_reboots = AsyncMock(return_value=None)
        mock_mgr.cleanup_stale_intents = AsyncMock(return_value=0)
        with patch("elle.daemon.reboot.get_manager", return_value=mock_mgr):
            await daemon._check_reboot_recovery()

    async def test_pending_intent_improved(self, daemon):
        mock_intent = MagicMock()
        mock_intent.id = "i1"
        mock_intent.status = "completed"
        mock_intent.outcome = "improved"
        mock_intent.incident_id = "inc1"
        mock_intent.goal = "fix"

        mock_mgr = MagicMock()
        mock_mgr.check_pending_reboots = AsyncMock(return_value=mock_intent)
        mock_mgr.cleanup_stale_intents = AsyncMock(return_value=0)

        with patch("elle.daemon.reboot.get_manager", return_value=mock_mgr):
            with patch.object(daemon, "_update_incident_with_reboot_result", new_callable=AsyncMock) as mock_update:
                await daemon._check_reboot_recovery()
                mock_update.assert_awaited_once()


# ---------------------------------------------------------------------------
# _update_incident_with_reboot_result
# ---------------------------------------------------------------------------


class TestUpdateIncidentWithRebootResult:
    async def test_improved(self, daemon):
        mock_intent = MagicMock()
        mock_intent.outcome = "improved"
        mock_intent.incident_id = "inc1"
        mock_intent.goal = "fix disk"
        with patch("elle.daemon.incidents.store.update_incident") as mock_update:
            await daemon._update_incident_with_reboot_result(mock_intent)
        mock_update.assert_called_once()

    async def test_failed(self, daemon):
        mock_intent = MagicMock()
        mock_intent.outcome = "failed"
        mock_intent.incident_id = "inc1"
        mock_intent.outcome_detail = "bad"
        with patch("elle.daemon.incidents.store.update_incident") as mock_update:
            await daemon._update_incident_with_reboot_result(mock_intent)
        mock_update.assert_called_once()

    async def test_exception(self, daemon):
        mock_intent = MagicMock()
        mock_intent.outcome = "improved"
        mock_intent.incident_id = "inc1"
        mock_intent.goal = "fix"
        with patch("elle.daemon.incidents.store.update_incident", side_effect=RuntimeError("fail")):
            await daemon._update_incident_with_reboot_result(mock_intent)


# ---------------------------------------------------------------------------
# _notify_incident
# ---------------------------------------------------------------------------


class TestNotifyIncident:
    async def test_low_severity_skipped(self, daemon):
        await daemon._notify_incident("inc1", "test", "warning", "disk")

    async def test_error_severity_notifies(self, daemon):
        mock_mod = MagicMock()
        mock_notify = MagicMock()
        mock_mod.notify_incident = mock_notify
        with patch.dict("sys.modules", {"elle.daemon.notifications": mock_mod}):
            await daemon._notify_incident("inc1", "test", "error", "disk")
            mock_notify.assert_called_once()

    async def test_import_error(self, daemon):
        with patch.dict("sys.modules", {"elle.daemon.notifications": None}):
            await daemon._notify_incident("inc1", "test", "critical", "disk")


# ---------------------------------------------------------------------------
# _handle_package_event
# ---------------------------------------------------------------------------


class TestHandlePackageEvent:
    async def test_no_versioner(self, daemon):
        daemon._capability_versioner = None
        event = _mock_event(category="pkg", raw={"package_name": "nginx", "new_version": "1.2"})
        await daemon._handle_package_event(event)

    async def test_missing_package_name(self, daemon):
        daemon._capability_versioner = MagicMock()
        event = _mock_event(category="pkg", raw={})
        await daemon._handle_package_event(event)

    async def test_successful_regeneration(self, daemon):
        mock_versioner = MagicMock()
        mock_versioner.on_package_upgraded = AsyncMock(return_value=["cap1"])
        daemon._capability_versioner = mock_versioner
        event = _mock_event(category="pkg", raw={"package_name": "nginx", "new_version": "1.2", "old_version": "1.0"})
        await daemon._handle_package_event(event)
        mock_versioner.on_package_upgraded.assert_awaited_once()


# ---------------------------------------------------------------------------
# _route_to_reactive
# ---------------------------------------------------------------------------


class TestRouteToReactive:
    async def test_import_error(self, daemon):
        events = [_mock_event()]
        with patch.dict("sys.modules", {"elle.reactive.router": None}):
            await daemon._route_to_reactive(events)

    async def test_successful_route(self, daemon):
        mock_router = MagicMock()
        mock_router.route = AsyncMock()
        mock_mod = MagicMock()
        mock_mod.get_router.return_value = mock_router
        with patch.dict("sys.modules", {"elle.reactive.router": mock_mod}):
            events = [_mock_event()]
            await daemon._route_to_reactive(events)
            mock_router.route.assert_awaited_once()


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------


class TestSetupLogging:
    def test_setup_default(self):
        setup_logging("INFO")
        assert logging.getLogger("uvicorn").level == logging.WARNING

    def test_setup_debug(self):
        setup_logging("DEBUG")


# ---------------------------------------------------------------------------
# _run_api
# ---------------------------------------------------------------------------


class TestRunApi:
    async def test_import_error(self, daemon):
        with patch.dict("sys.modules", {"uvicorn": None}):
            await daemon._run_api()

    async def test_cancelled(self, daemon):
        mock_uvicorn = MagicMock()
        mock_server = MagicMock()
        mock_server.serve = AsyncMock(side_effect=asyncio.CancelledError)
        mock_uvicorn.Server.return_value = mock_server
        mock_uvicorn.Config = MagicMock()
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            with patch("elle.daemon.api.app.create_app", return_value=MagicMock()):
                await daemon._run_api()


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


class TestRun:
    async def test_run_calls_start_and_stop(self, daemon):
        with patch.object(daemon, "start", new_callable=AsyncMock) as mock_start:
            with patch.object(daemon, "stop", new_callable=AsyncMock) as mock_stop:
                # Set shutdown immediately
                daemon.shutdown.set()
                await daemon.run()
                mock_start.assert_awaited_once()
                mock_stop.assert_awaited_once()
