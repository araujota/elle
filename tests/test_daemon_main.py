from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.config import ApiConfig, CloudSyncConfig, Config, QueueConfig
from elle.daemon.main import (
    ElledDaemon,
    main,
    run_daemon,
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
        cloud_sync=CloudSyncConfig(enabled=False),
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


# ---------------------------------------------------------------------------
# _correlate_events
# ---------------------------------------------------------------------------


class TestCorrelateEvents:
    async def test_correlate_warning_creates_incident(self, daemon):
        mock_correlator = MagicMock()
        mock_correlator.process_event.return_value = "inc-123"
        with patch("elle.daemon.incidents.correlator.get_correlator", return_value=mock_correlator):
            with patch.object(daemon, "_notify_incident", new_callable=AsyncMock) as mock_notify:
                events = [_mock_event(severity="warning")]
                await daemon._correlate_events(events)
                mock_correlator.process_event.assert_called_once()
                assert daemon._incidents_total == 1
                mock_notify.assert_awaited_once()

    async def test_correlate_error_creates_incident(self, daemon):
        mock_correlator = MagicMock()
        mock_correlator.process_event.return_value = "inc-456"
        with patch("elle.daemon.incidents.correlator.get_correlator", return_value=mock_correlator):
            with patch.object(daemon, "_notify_incident", new_callable=AsyncMock):
                events = [_mock_event(severity="error")]
                await daemon._correlate_events(events)
                assert daemon._incidents_total == 1

    async def test_correlate_critical_creates_incident(self, daemon):
        mock_correlator = MagicMock()
        mock_correlator.process_event.return_value = "inc-789"
        with patch("elle.daemon.incidents.correlator.get_correlator", return_value=mock_correlator):
            with patch.object(daemon, "_notify_incident", new_callable=AsyncMock):
                events = [_mock_event(severity="critical")]
                await daemon._correlate_events(events)
                assert daemon._incidents_total == 1

    async def test_correlate_info_skipped(self, daemon):
        mock_correlator = MagicMock()
        with patch("elle.daemon.incidents.correlator.get_correlator", return_value=mock_correlator):
            events = [_mock_event(severity="info")]
            await daemon._correlate_events(events)
            mock_correlator.process_event.assert_not_called()
            assert daemon._incidents_total == 0

    async def test_correlate_no_incident_returned(self, daemon):
        mock_correlator = MagicMock()
        mock_correlator.process_event.return_value = None
        with patch("elle.daemon.incidents.correlator.get_correlator", return_value=mock_correlator):
            events = [_mock_event(severity="error")]
            await daemon._correlate_events(events)
            assert daemon._incidents_total == 0

    async def test_correlate_exception_handled(self, daemon):
        mock_correlator = MagicMock()
        mock_correlator.process_event.side_effect = RuntimeError("DB error")
        with patch("elle.daemon.incidents.correlator.get_correlator", return_value=mock_correlator):
            events = [_mock_event(severity="error")]
            await daemon._correlate_events(events)
            assert daemon._incidents_total == 0

    async def test_correlate_multiple_events(self, daemon):
        mock_correlator = MagicMock()
        mock_correlator.process_event.side_effect = ["inc-1", None, "inc-3"]
        with patch("elle.daemon.incidents.correlator.get_correlator", return_value=mock_correlator):
            with patch.object(daemon, "_notify_incident", new_callable=AsyncMock):
                events = [
                    _mock_event(severity="warning"),
                    _mock_event(severity="error"),
                    _mock_event(severity="critical"),
                ]
                await daemon._correlate_events(events)
                assert daemon._incidents_total == 2


# ---------------------------------------------------------------------------
# _processor_loop
# ---------------------------------------------------------------------------


class TestProcessorLoop:
    async def test_processor_no_queue_sleeps(self, daemon):
        daemon.event_queue = None

        # Set shutdown after a brief moment
        async def set_shutdown():
            await asyncio.sleep(0.2)
            daemon.shutdown.set()

        asyncio.create_task(set_shutdown())
        await daemon._processor_loop()

    async def test_processor_empty_batch(self, daemon):
        mock_queue = MagicMock()
        mock_queue.get_batch = AsyncMock(return_value=[])
        daemon.event_queue = mock_queue

        async def set_shutdown():
            await asyncio.sleep(0.2)
            daemon.shutdown.set()

        asyncio.create_task(set_shutdown())
        await daemon._processor_loop()

    async def test_processor_processes_events(self, daemon):
        events = [_mock_event(severity="info")]
        mock_queue = MagicMock()
        call_count = 0

        async def get_batch_side_effect(max_items=100, timeout=1.0):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return events
            daemon.shutdown.set()
            return []

        mock_queue.get_batch = AsyncMock(side_effect=get_batch_side_effect)
        daemon.event_queue = mock_queue

        with patch("elle.daemon.main.insert_events_batch", return_value=1) as mock_insert:
            with patch.object(daemon, "_correlate_events", new_callable=AsyncMock) as mock_corr:
                with patch.object(daemon, "_route_to_reactive", new_callable=AsyncMock) as mock_route:
                    await daemon._processor_loop()
                    mock_insert.assert_called_once_with(events)
                    mock_corr.assert_awaited_once()
                    mock_route.assert_awaited_once()
                    assert daemon._events_total == 1

    async def test_processor_store_failure(self, daemon, caplog):
        events = [_mock_event()]
        mock_queue = MagicMock()
        call_count = 0

        async def get_batch_side_effect(max_items=100, timeout=1.0):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return events
            daemon.shutdown.set()
            return []

        mock_queue.get_batch = AsyncMock(side_effect=get_batch_side_effect)
        daemon.event_queue = mock_queue

        with patch("elle.daemon.main.insert_events_batch", side_effect=RuntimeError("DB fail")):
            with patch.object(daemon, "_correlate_events", new_callable=AsyncMock):
                with patch.object(daemon, "_route_to_reactive", new_callable=AsyncMock):
                    with caplog.at_level(logging.ERROR):
                        await daemon._processor_loop()
                    assert "Failed to store events" in caplog.text

    async def test_processor_correlate_failure(self, daemon, caplog):
        events = [_mock_event()]
        mock_queue = MagicMock()
        call_count = 0

        async def get_batch_side_effect(max_items=100, timeout=1.0):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return events
            daemon.shutdown.set()
            return []

        mock_queue.get_batch = AsyncMock(side_effect=get_batch_side_effect)
        daemon.event_queue = mock_queue

        with patch("elle.daemon.main.insert_events_batch", return_value=1):
            with patch.object(
                daemon, "_correlate_events", new_callable=AsyncMock, side_effect=RuntimeError("corr fail")
            ):
                with patch.object(daemon, "_route_to_reactive", new_callable=AsyncMock):
                    with caplog.at_level(logging.ERROR):
                        await daemon._processor_loop()
                    assert "Failed to correlate events" in caplog.text

    async def test_processor_route_failure(self, daemon, caplog):
        events = [_mock_event()]
        mock_queue = MagicMock()
        call_count = 0

        async def get_batch_side_effect(max_items=100, timeout=1.0):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return events
            daemon.shutdown.set()
            return []

        mock_queue.get_batch = AsyncMock(side_effect=get_batch_side_effect)
        daemon.event_queue = mock_queue

        with patch("elle.daemon.main.insert_events_batch", return_value=1):
            with patch.object(daemon, "_correlate_events", new_callable=AsyncMock):
                with patch.object(
                    daemon, "_route_to_reactive", new_callable=AsyncMock, side_effect=RuntimeError("route fail")
                ):
                    with caplog.at_level(logging.ERROR):
                        await daemon._processor_loop()
                    assert "Failed to route events" in caplog.text

    async def test_processor_handles_pkg_events_with_versioning(self, daemon, caplog):
        daemon.config = Config(
            api=ApiConfig(enabled=False),
            queues=QueueConfig(raw_queue_size=100, event_queue_size=50),
            capability_versioning_enabled=True,
            capability_bootstrap_enabled=False,
        )
        events = [_mock_event(category="pkg", severity="info")]
        mock_queue = MagicMock()
        call_count = 0

        async def get_batch_side_effect(max_items=100, timeout=1.0):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return events
            daemon.shutdown.set()
            return []

        mock_queue.get_batch = AsyncMock(side_effect=get_batch_side_effect)
        daemon.event_queue = mock_queue

        with patch("elle.daemon.main.insert_events_batch", return_value=1):
            with patch.object(daemon, "_correlate_events", new_callable=AsyncMock):
                with patch.object(daemon, "_route_to_reactive", new_callable=AsyncMock):
                    with patch.object(daemon, "_handle_package_event", new_callable=AsyncMock) as mock_handle:
                        await daemon._processor_loop()
                        mock_handle.assert_awaited_once()

    async def test_processor_pkg_event_handler_exception(self, daemon, caplog):
        daemon.config = Config(
            api=ApiConfig(enabled=False),
            queues=QueueConfig(raw_queue_size=100, event_queue_size=50),
            capability_versioning_enabled=True,
            capability_bootstrap_enabled=False,
        )
        events = [_mock_event(category="pkg", severity="info")]
        mock_queue = MagicMock()
        call_count = 0

        async def get_batch_side_effect(max_items=100, timeout=1.0):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return events
            daemon.shutdown.set()
            return []

        mock_queue.get_batch = AsyncMock(side_effect=get_batch_side_effect)
        daemon.event_queue = mock_queue

        with patch("elle.daemon.main.insert_events_batch", return_value=1):
            with patch.object(daemon, "_correlate_events", new_callable=AsyncMock):
                with patch.object(daemon, "_route_to_reactive", new_callable=AsyncMock):
                    with patch.object(
                        daemon, "_handle_package_event", new_callable=AsyncMock, side_effect=RuntimeError("pkg fail")
                    ):
                        await daemon._processor_loop()

    async def test_processor_cancelled_error(self, daemon):
        mock_queue = MagicMock()
        mock_queue.get_batch = AsyncMock(side_effect=asyncio.CancelledError)
        daemon.event_queue = mock_queue
        await daemon._processor_loop()

    async def test_processor_general_exception_continues(self, daemon, caplog):
        mock_queue = MagicMock()
        call_count = 0

        async def get_batch_side_effect(max_items=100, timeout=1.0):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("unexpected error")
            daemon.shutdown.set()
            return []

        mock_queue.get_batch = AsyncMock(side_effect=get_batch_side_effect)
        daemon.event_queue = mock_queue

        with caplog.at_level(logging.ERROR):
            await daemon._processor_loop()
        assert "Processor error" in caplog.text


# ---------------------------------------------------------------------------
# _start_capability_versioning
# ---------------------------------------------------------------------------


class TestStartCapabilityVersioning:
    async def test_successful_start(self, daemon):
        mock_store = MagicMock()
        mock_versioner = MagicMock()
        mock_versioner.build_package_map.return_value = {"nginx": ["cap1"]}

        with patch("elle.capabilities.autogen.store.get_store", return_value=mock_store):
            with patch("elle.capabilities.autogen.versioner.CapabilityVersioner", return_value=mock_versioner):
                await daemon._start_capability_versioning()

        assert daemon._capability_versioner is mock_versioner

    async def test_import_error(self, daemon, caplog):
        with patch.dict("sys.modules", {"elle.capabilities.autogen.store": None}):
            await daemon._start_capability_versioning()
        assert daemon._capability_versioner is None

    async def test_general_exception(self, daemon, caplog):
        with patch("elle.capabilities.autogen.store.get_store", side_effect=RuntimeError("store fail")):
            with caplog.at_level(logging.WARNING):
                await daemon._start_capability_versioning()
            assert "Failed to start capability versioning" in caplog.text


# ---------------------------------------------------------------------------
# _run_capability_bootstrap
# ---------------------------------------------------------------------------


class TestRunCapabilityBootstrap:
    async def test_bootstrap_already_complete(self, daemon, caplog):
        mock_mod = MagicMock()
        mock_mod.should_run_bootstrap.return_value = False
        with patch.dict("sys.modules", {"elle.capabilities.autogen.bootstrap": mock_mod}):
            with caplog.at_level(logging.DEBUG):
                await daemon._run_capability_bootstrap()

    async def test_bootstrap_runs_successfully(self, daemon, caplog):
        mock_result = MagicMock()
        mock_result.packages_succeeded = 10
        mock_result.packages_attempted = 10
        mock_result.capabilities_saved = 50
        mock_result.duration_seconds = 5.0
        mock_result.failed_packages = []

        mock_mod = MagicMock()
        mock_mod.should_run_bootstrap.return_value = True
        mock_mod.run_bootstrap = AsyncMock(return_value=mock_result)
        mock_mod.set_bootstrap_complete = MagicMock()

        with patch.dict("sys.modules", {"elle.capabilities.autogen.bootstrap": mock_mod}):
            with caplog.at_level(logging.INFO):
                await daemon._run_capability_bootstrap()
            mock_mod.set_bootstrap_complete.assert_called_once_with(mock_result)

    async def test_bootstrap_with_failures(self, daemon, caplog):
        mock_result = MagicMock()
        mock_result.packages_succeeded = 8
        mock_result.packages_attempted = 10
        mock_result.capabilities_saved = 40
        mock_result.duration_seconds = 10.0
        mock_result.failed_packages = ["pkg1", "pkg2"]

        mock_mod = MagicMock()
        mock_mod.should_run_bootstrap.return_value = True
        mock_mod.run_bootstrap = AsyncMock(return_value=mock_result)
        mock_mod.set_bootstrap_complete = MagicMock()

        with patch.dict("sys.modules", {"elle.capabilities.autogen.bootstrap": mock_mod}):
            with caplog.at_level(logging.WARNING):
                await daemon._run_capability_bootstrap()
            assert "failed" in caplog.text.lower()

    async def test_bootstrap_import_error(self, daemon, caplog):
        with patch.dict("sys.modules", {"elle.capabilities.autogen.bootstrap": None}):
            await daemon._run_capability_bootstrap()

    async def test_bootstrap_cancelled(self, daemon):
        mock_mod = MagicMock()
        mock_mod.should_run_bootstrap.return_value = True
        mock_mod.run_bootstrap = AsyncMock(side_effect=asyncio.CancelledError)

        with patch.dict("sys.modules", {"elle.capabilities.autogen.bootstrap": mock_mod}):
            await daemon._run_capability_bootstrap()

    async def test_bootstrap_general_exception(self, daemon, caplog):
        mock_mod = MagicMock()
        mock_mod.should_run_bootstrap.return_value = True
        mock_mod.run_bootstrap = AsyncMock(side_effect=RuntimeError("bootstrap fail"))

        with patch.dict("sys.modules", {"elle.capabilities.autogen.bootstrap": mock_mod}):
            with caplog.at_level(logging.ERROR):
                await daemon._run_capability_bootstrap()
            assert "Capability bootstrap failed" in caplog.text


# ---------------------------------------------------------------------------
# _start_cloud_retry_worker
# ---------------------------------------------------------------------------


class TestStartCloudRetryWorker:
    async def test_cloud_sync_disabled(self, daemon, caplog):
        daemon.config = Config(
            api=ApiConfig(enabled=False),
            queues=QueueConfig(raw_queue_size=100, event_queue_size=50),
            cloud_sync=CloudSyncConfig(enabled=False),
        )
        with caplog.at_level(logging.INFO):
            await daemon._start_cloud_retry_worker()
        assert "Cloud sync disabled" in caplog.text

    async def test_cloud_sync_enabled_success(self, daemon):
        daemon.config = Config(
            api=ApiConfig(enabled=False),
            queues=QueueConfig(raw_queue_size=100, event_queue_size=50),
            cloud_sync=CloudSyncConfig(enabled=True),
        )
        mock_worker = MagicMock()
        mock_worker.start = AsyncMock()

        with patch("elle.daemon.incidents.cloud_queue.configure_cloud_queue"):
            with patch("elle.daemon.incidents.cloud_retry_worker.CloudRetryWorker", return_value=mock_worker):
                await daemon._start_cloud_retry_worker()
        assert daemon._cloud_retry_worker is mock_worker
        mock_worker.start.assert_awaited_once()

    async def test_cloud_sync_exception(self, daemon, caplog):
        daemon.config = Config(
            api=ApiConfig(enabled=False),
            queues=QueueConfig(raw_queue_size=100, event_queue_size=50),
            cloud_sync=CloudSyncConfig(enabled=True),
        )
        with patch("elle.daemon.incidents.cloud_queue.configure_cloud_queue", side_effect=RuntimeError("cloud fail")):
            with caplog.at_level(logging.WARNING):
                await daemon._start_cloud_retry_worker()
            assert "Failed to start cloud retry worker" in caplog.text


# ---------------------------------------------------------------------------
# _handle_package_event - additional coverage
# ---------------------------------------------------------------------------


class TestHandlePackageEventExtended:
    async def test_missing_new_version(self, daemon):
        daemon._capability_versioner = MagicMock()
        event = _mock_event(category="pkg", raw={"package_name": "nginx"})
        await daemon._handle_package_event(event)
        daemon._capability_versioner.on_package_upgraded.assert_not_called()

    async def test_exception_in_versioner(self, daemon, caplog):
        mock_versioner = MagicMock()
        mock_versioner.on_package_upgraded = AsyncMock(side_effect=RuntimeError("regen fail"))
        daemon._capability_versioner = mock_versioner
        event = _mock_event(
            category="pkg",
            raw={"package_name": "nginx", "new_version": "2.0", "old_version": "1.0"},
        )
        with caplog.at_level(logging.ERROR):
            await daemon._handle_package_event(event)
        assert "Failed to handle package upgrade" in caplog.text

    async def test_regenerated_empty_list(self, daemon):
        mock_versioner = MagicMock()
        mock_versioner.on_package_upgraded = AsyncMock(return_value=[])
        daemon._capability_versioner = mock_versioner
        event = _mock_event(
            category="pkg",
            raw={"package_name": "nginx", "new_version": "2.0", "old_version": "1.0"},
        )
        await daemon._handle_package_event(event)


# ---------------------------------------------------------------------------
# _notify_incident - additional coverage
# ---------------------------------------------------------------------------


class TestNotifyIncidentExtended:
    async def test_critical_severity_notifies(self, daemon):
        mock_mod = MagicMock()
        with patch.dict("sys.modules", {"elle.daemon.notifications": mock_mod}):
            await daemon._notify_incident("inc1", "test", "critical", "disk")
            mock_mod.notify_incident.assert_called_once()

    async def test_notify_exception_handled(self, daemon, caplog):
        mock_mod = MagicMock()
        mock_mod.notify_incident.side_effect = RuntimeError("notify fail")
        with patch.dict("sys.modules", {"elle.daemon.notifications": mock_mod}):
            with caplog.at_level(logging.DEBUG):
                await daemon._notify_incident("inc1", "test", "error", "disk")

    async def test_info_severity_skipped(self, daemon):
        await daemon._notify_incident("inc1", "test", "info", "disk")

    async def test_notice_severity_skipped(self, daemon):
        await daemon._notify_incident("inc1", "test", "notice", "disk")


# ---------------------------------------------------------------------------
# _check_reboot_recovery - additional coverage
# ---------------------------------------------------------------------------


class TestCheckRebootRecoveryExtended:
    async def test_intent_with_unknown_outcome_no_update(self, daemon):
        mock_intent = MagicMock()
        mock_intent.id = "i1"
        mock_intent.status = "completed"
        mock_intent.outcome = "unknown"
        mock_intent.incident_id = "inc1"

        mock_mgr = MagicMock()
        mock_mgr.check_pending_reboots = AsyncMock(return_value=mock_intent)
        mock_mgr.cleanup_stale_intents = AsyncMock(return_value=0)

        with patch("elle.daemon.reboot.get_manager", return_value=mock_mgr):
            with patch.object(daemon, "_update_incident_with_reboot_result", new_callable=AsyncMock) as mock_update:
                await daemon._check_reboot_recovery()
                mock_update.assert_not_awaited()

    async def test_intent_without_incident_id_no_update(self, daemon):
        mock_intent = MagicMock()
        mock_intent.id = "i1"
        mock_intent.status = "completed"
        mock_intent.outcome = "improved"
        mock_intent.incident_id = None

        mock_mgr = MagicMock()
        mock_mgr.check_pending_reboots = AsyncMock(return_value=mock_intent)
        mock_mgr.cleanup_stale_intents = AsyncMock(return_value=0)

        with patch("elle.daemon.reboot.get_manager", return_value=mock_mgr):
            with patch.object(daemon, "_update_incident_with_reboot_result", new_callable=AsyncMock) as mock_update:
                await daemon._check_reboot_recovery()
                mock_update.assert_not_awaited()

    async def test_stale_intents_cleaned(self, daemon, caplog):
        mock_mgr = MagicMock()
        mock_mgr.check_pending_reboots = AsyncMock(return_value=None)
        mock_mgr.cleanup_stale_intents = AsyncMock(return_value=3)

        with patch("elle.daemon.reboot.get_manager", return_value=mock_mgr):
            with caplog.at_level(logging.WARNING):
                await daemon._check_reboot_recovery()
            assert "Cleaned up 3 stale reboot intents" in caplog.text

    async def test_general_exception(self, daemon, caplog):
        with patch("elle.daemon.reboot.get_manager", side_effect=RuntimeError("reboot fail")):
            with caplog.at_level(logging.ERROR):
                await daemon._check_reboot_recovery()
            assert "Failed to check reboot recovery" in caplog.text


# ---------------------------------------------------------------------------
# _update_incident_with_reboot_result - additional coverage
# ---------------------------------------------------------------------------


class TestUpdateIncidentExtended:
    async def test_rolled_back_outcome(self, daemon):
        mock_intent = MagicMock()
        mock_intent.outcome = "rolled_back"
        mock_intent.incident_id = "inc1"
        mock_intent.outcome_detail = "reverted changes"
        with patch("elle.daemon.incidents.store.update_incident") as mock_update:
            await daemon._update_incident_with_reboot_result(mock_intent)
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[1]["outcome"] == "partial"

    async def test_other_outcome_does_nothing(self, daemon):
        mock_intent = MagicMock()
        mock_intent.outcome = "pending"
        mock_intent.incident_id = "inc1"
        with patch("elle.daemon.incidents.store.update_incident") as mock_update:
            await daemon._update_incident_with_reboot_result(mock_intent)
        mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# _route_to_reactive - additional coverage
# ---------------------------------------------------------------------------


class TestRouteToReactiveExtended:
    async def test_route_per_event_exception(self, daemon, caplog):
        mock_router = MagicMock()
        mock_router.route = AsyncMock(side_effect=RuntimeError("route fail"))
        mock_mod = MagicMock()
        mock_mod.get_router.return_value = mock_router
        with patch.dict("sys.modules", {"elle.reactive.router": mock_mod}):
            events = [_mock_event(), _mock_event()]
            await daemon._route_to_reactive(events)


# ---------------------------------------------------------------------------
# stop - additional coverage
# ---------------------------------------------------------------------------


class TestStopExtended:
    async def test_stop_cloud_retry_worker(self, daemon):
        daemon.started_at = datetime.now(timezone.utc)
        mock_worker = MagicMock()
        mock_worker.stop = AsyncMock()
        daemon._cloud_retry_worker = mock_worker
        await daemon.stop()
        mock_worker.stop.assert_awaited_once()

    async def test_stop_cloud_retry_worker_error(self, daemon, caplog):
        daemon.started_at = datetime.now(timezone.utc)
        mock_worker = MagicMock()
        mock_worker.stop = AsyncMock(side_effect=RuntimeError("cloud stop fail"))
        daemon._cloud_retry_worker = mock_worker
        with caplog.at_level(logging.WARNING):
            await daemon.stop()
        assert "Error stopping cloud retry worker" in caplog.text

    async def test_stop_task_timeout(self, daemon, caplog):
        daemon.started_at = datetime.now(timezone.utc)

        async def _never_ending():
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                # Simulate a task that doesn't stop quickly
                await asyncio.sleep(10)

        task = asyncio.create_task(_never_ending())
        daemon._tasks = [task]

        with caplog.at_level(logging.WARNING):
            await daemon.stop()
        # The timeout may or may not be hit depending on timing,
        # but the stop should still complete


# ---------------------------------------------------------------------------
# _warmup_models - additional coverage
# ---------------------------------------------------------------------------


class TestWarmupModelsExtended:
    async def test_model_not_ready(self, daemon, caplog):
        mock_service = MagicMock()
        mock_service.ensure_model_ready = AsyncMock(return_value=False)

        with patch("elle.rag.model_warmup.ModelWarmupService", return_value=mock_service):
            with caplog.at_level(logging.WARNING):
                await daemon._warmup_models()
        assert "not available" in caplog.text

    async def test_model_ready_warmup_success(self, daemon, caplog):
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.model = "qwen2.5"
        mock_result.duration_ms = 150.0

        mock_service = MagicMock()
        mock_service.ensure_model_ready = AsyncMock(return_value=True)
        mock_service.warm_llm = AsyncMock(return_value=mock_result)
        mock_service.start_periodic_warmup = AsyncMock()

        with patch("elle.rag.model_warmup.ModelWarmupService", return_value=mock_service):
            with caplog.at_level(logging.INFO):
                await daemon._warmup_models()
        mock_service.start_periodic_warmup.assert_awaited_once()

    async def test_model_ready_warmup_failure(self, daemon, caplog):
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error = "timeout"

        mock_service = MagicMock()
        mock_service.ensure_model_ready = AsyncMock(return_value=True)
        mock_service.warm_llm = AsyncMock(return_value=mock_result)

        with patch("elle.rag.model_warmup.ModelWarmupService", return_value=mock_service):
            with caplog.at_level(logging.WARNING):
                await daemon._warmup_models()
        assert "warmup failed" in caplog.text.lower()


# ---------------------------------------------------------------------------
# _start_notification_service - additional coverage
# ---------------------------------------------------------------------------


class TestStartNotificationExtended:
    async def test_successful_start(self, daemon, caplog):
        mock_svc = MagicMock()
        mock_svc.start = AsyncMock()
        mock_mod = MagicMock()
        mock_mod.get_service.return_value = mock_svc
        with patch.dict("sys.modules", {"elle.daemon.notifications": mock_mod}):
            with caplog.at_level(logging.INFO):
                await daemon._start_notification_service()
            assert "Notification service started" in caplog.text
            assert daemon._notification_service is mock_svc


# ---------------------------------------------------------------------------
# _run_api - additional coverage
# ---------------------------------------------------------------------------


class TestRunApiExtended:
    async def test_server_exception(self, daemon, caplog):
        mock_uvicorn = MagicMock()
        mock_server = MagicMock()
        mock_server.serve = AsyncMock(side_effect=RuntimeError("bind failed"))
        mock_uvicorn.Server.return_value = mock_server
        mock_uvicorn.Config = MagicMock()
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            with patch("elle.daemon.api.app.create_app", return_value=MagicMock()):
                with caplog.at_level(logging.ERROR):
                    await daemon._run_api()
                assert "API server error" in caplog.text


# ---------------------------------------------------------------------------
# setup_logging - additional coverage
# ---------------------------------------------------------------------------


class TestSetupLoggingExtended:
    def test_warning_level(self):
        setup_logging("WARNING")
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_error_level(self):
        setup_logging("ERROR")
        root = logging.getLogger()
        assert root.level == logging.ERROR

    def test_invalid_level_defaults_to_info(self):
        setup_logging("NONEXISTENT")
        root = logging.getLogger()
        assert root.level == logging.INFO


# ---------------------------------------------------------------------------
# run_daemon
# ---------------------------------------------------------------------------


class TestRunDaemon:
    async def test_run_daemon_creates_and_runs(self):
        with patch("elle.daemon.main.ElledDaemon") as MockDaemon:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock()
            mock_instance.shutdown = asyncio.Event()
            MockDaemon.return_value = mock_instance

            # run_daemon accesses the event loop, so we patch that
            mock_loop = MagicMock()
            mock_loop.add_signal_handler = MagicMock()
            mock_loop.remove_signal_handler = MagicMock()
            with patch("asyncio.get_event_loop", return_value=mock_loop):
                await run_daemon(Config())
            mock_instance.run.assert_awaited_once()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    @patch("elle.daemon.main.asyncio.run")
    @patch("elle.daemon.main.set_config")
    @patch("elle.daemon.main.load_config")
    @patch("elle.daemon.main.setup_logging")
    @patch("argparse.ArgumentParser.parse_args")
    def test_main_defaults(self, mock_args, mock_setup, mock_load, mock_set, mock_run):
        mock_args.return_value = MagicMock(config=None, log_level="INFO", no_api=False)
        mock_load.return_value = Config()
        result = main()
        assert result == 0
        mock_setup.assert_called_once_with("INFO")
        mock_load.assert_called_once()
        mock_set.assert_called_once()
        mock_run.assert_called_once()

    @patch("elle.daemon.main.asyncio.run")
    @patch("elle.daemon.main.set_config")
    @patch("elle.daemon.main.load_config")
    @patch("elle.daemon.main.setup_logging")
    @patch("argparse.ArgumentParser.parse_args")
    def test_main_no_api_flag(self, mock_args, mock_setup, mock_load, mock_set, mock_run):
        mock_args.return_value = MagicMock(config=None, log_level="DEBUG", no_api=True)
        mock_load.return_value = Config()
        result = main()
        assert result == 0
        # Verify that api was set to disabled
        call_args = mock_set.call_args[0][0]
        assert call_args.api.enabled is False

    @patch("elle.daemon.main.asyncio.run", side_effect=KeyboardInterrupt)
    @patch("elle.daemon.main.set_config")
    @patch("elle.daemon.main.load_config")
    @patch("elle.daemon.main.setup_logging")
    @patch("argparse.ArgumentParser.parse_args")
    def test_main_keyboard_interrupt(self, mock_args, mock_setup, mock_load, mock_set, mock_run):
        mock_args.return_value = MagicMock(config=None, log_level="INFO", no_api=False)
        mock_load.return_value = Config()
        result = main()
        assert result == 0

    @patch("elle.daemon.main.asyncio.run", side_effect=RuntimeError("telemetryd not available"))
    @patch("elle.daemon.main.set_config")
    @patch("elle.daemon.main.load_config")
    @patch("elle.daemon.main.setup_logging")
    @patch("argparse.ArgumentParser.parse_args")
    def test_main_telemetryd_error(self, mock_args, mock_setup, mock_load, mock_set, mock_run):
        mock_args.return_value = MagicMock(config=None, log_level="INFO", no_api=False)
        mock_load.return_value = Config()
        result = main()
        assert result == 1

    @patch("elle.daemon.main.asyncio.run", side_effect=RuntimeError("some other error"))
    @patch("elle.daemon.main.set_config")
    @patch("elle.daemon.main.load_config")
    @patch("elle.daemon.main.setup_logging")
    @patch("argparse.ArgumentParser.parse_args")
    def test_main_other_runtime_error(self, mock_args, mock_setup, mock_load, mock_set, mock_run):
        mock_args.return_value = MagicMock(config=None, log_level="INFO", no_api=False)
        mock_load.return_value = Config()
        with pytest.raises(RuntimeError, match="some other error"):
            main()

    @patch("elle.daemon.main.asyncio.run", side_effect=OSError("daemon failed"))
    @patch("elle.daemon.main.set_config")
    @patch("elle.daemon.main.load_config")
    @patch("elle.daemon.main.setup_logging")
    @patch("argparse.ArgumentParser.parse_args")
    def test_main_general_exception(self, mock_args, mock_setup, mock_load, mock_set, mock_run):
        mock_args.return_value = MagicMock(config=None, log_level="INFO", no_api=False)
        mock_load.return_value = Config()
        result = main()
        assert result == 1

    @patch("elle.daemon.main.asyncio.run")
    @patch("elle.daemon.main.set_config")
    @patch("elle.daemon.main.load_config")
    @patch("elle.daemon.main.setup_logging")
    @patch("argparse.ArgumentParser.parse_args")
    def test_main_with_config_path(self, mock_args, mock_setup, mock_load, mock_set, mock_run):
        mock_args.return_value = MagicMock(config=Path("/etc/elle/custom.toml"), log_level="INFO", no_api=False)
        mock_load.return_value = Config()
        result = main()
        assert result == 0
        mock_load.assert_called_once_with(Path("/etc/elle/custom.toml"))


# ---------------------------------------------------------------------------
# get_status - additional coverage
# ---------------------------------------------------------------------------


class TestGetStatusExtended:
    def test_status_api_enabled(self, daemon):
        daemon.config = Config(
            api=ApiConfig(enabled=True),
            queues=QueueConfig(raw_queue_size=100, event_queue_size=50),
        )
        status = daemon.get_status()
        assert status.api_active is True

    def test_status_api_disabled(self, daemon):
        status = daemon.get_status()
        assert status.api_active is False

    def test_status_with_incidents(self, daemon):
        daemon._incidents_total = 7
        status = daemon.get_status()
        assert status.incidents_total == 7

    def test_status_no_watcher_no_errors(self, daemon):
        daemon._telemetryd_watcher = None
        status = daemon.get_status()
        assert len(status.errors) == 0
        assert status.healthy is True


# ---------------------------------------------------------------------------
# start - additional coverage
# ---------------------------------------------------------------------------


class TestStartExtended:
    @patch("elle.daemon.main.is_telemetryd_available", return_value=True)
    async def test_start_with_api_enabled(self, _, tmp_path):
        cfg = Config(
            api=ApiConfig(enabled=True, host="127.0.0.1", port=8377),
            queues=QueueConfig(raw_queue_size=100, event_queue_size=50),
            capability_versioning_enabled=False,
            capability_bootstrap_enabled=False,
        )
        daemon = ElledDaemon(config=cfg)

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
                                            with patch.object(daemon, "_run_api", new_callable=AsyncMock):
                                                with patch.object(
                                                    daemon, "_start_cloud_retry_worker", new_callable=AsyncMock
                                                ):
                                                    with patch.dict(
                                                        "sys.modules", {"elle.daemon.manvault.service": MagicMock()}
                                                    ):
                                                        await daemon.start()

        # API task should be created
        api_tasks = [t for t in daemon._tasks if t.get_name() == "api"]
        assert len(api_tasks) == 1

    @patch("elle.daemon.main.is_telemetryd_available", return_value=True)
    async def test_start_with_capability_versioning(self, _, tmp_path):
        cfg = Config(
            api=ApiConfig(enabled=False),
            queues=QueueConfig(raw_queue_size=100, event_queue_size=50),
            capability_versioning_enabled=True,
            capability_bootstrap_enabled=False,
        )
        daemon = ElledDaemon(config=cfg)

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
                                            with patch.object(
                                                daemon, "_start_capability_versioning", new_callable=AsyncMock
                                            ) as mock_cv:
                                                with patch.object(
                                                    daemon, "_start_cloud_retry_worker", new_callable=AsyncMock
                                                ):
                                                    with patch.dict(
                                                        "sys.modules", {"elle.daemon.manvault.service": MagicMock()}
                                                    ):
                                                        await daemon.start()

        mock_cv.assert_awaited_once()

    @patch("elle.daemon.main.is_telemetryd_available", return_value=True)
    async def test_start_with_bootstrap(self, _, tmp_path):
        cfg = Config(
            api=ApiConfig(enabled=False),
            queues=QueueConfig(raw_queue_size=100, event_queue_size=50),
            capability_versioning_enabled=False,
            capability_bootstrap_enabled=True,
        )
        daemon = ElledDaemon(config=cfg)

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
                                            with patch.object(
                                                daemon, "_run_capability_bootstrap", new_callable=AsyncMock
                                            ):
                                                with patch.object(
                                                    daemon, "_start_cloud_retry_worker", new_callable=AsyncMock
                                                ):
                                                    with patch.dict(
                                                        "sys.modules", {"elle.daemon.manvault.service": MagicMock()}
                                                    ):
                                                        await daemon.start()

        bootstrap_tasks = [t for t in daemon._tasks if t.get_name() == "capability_bootstrap"]
        assert len(bootstrap_tasks) == 1

    @patch("elle.daemon.main.is_telemetryd_available", return_value=True)
    async def test_start_manvault_failure(self, _, daemon, tmp_path, caplog):
        mock_token_mgr = MagicMock()
        mock_token_mgr.initialize = MagicMock()
        mock_token_mgr.token_path = tmp_path / "token"

        mock_state_cache = MagicMock()
        mock_state_cache.start = AsyncMock()

        mock_watcher = MagicMock()
        mock_watcher.start = AsyncMock()

        mock_manvault_mod = MagicMock()
        mock_manvault_svc = MagicMock()
        mock_manvault_svc.start = AsyncMock(side_effect=RuntimeError("manvault fail"))
        mock_manvault_mod.get_service.return_value = mock_manvault_svc

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
                                            with patch.object(
                                                daemon, "_start_cloud_retry_worker", new_callable=AsyncMock
                                            ):
                                                with patch.dict(
                                                    "sys.modules", {"elle.daemon.manvault.service": mock_manvault_mod}
                                                ):
                                                    with caplog.at_level(logging.WARNING):
                                                        await daemon.start()

        assert "Failed to start Man Vault service" in caplog.text
