from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.daemon.telemetry.models import TelemetryEvent
from elle.daemon.telemetry.telemetryd_watcher import (
    MAX_RESTART_ATTEMPTS,
    RESTART_COOLDOWN,
    STALE_THRESHOLD,
    TelemetrydWatcher,
    is_telemetryd_available,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raw_event(**overrides: Any) -> dict[str, Any]:
    """Build a raw NDJSON event dict as it arrives from telemetryd."""
    event: dict[str, Any] = {
        "ts": int(time.time() * 1e9),
        "source": "journal",
        "severity": "warning",
        "category": "disk",
        "message": "Disk usage exceeded 90%",
        "entity": "disk:/dev/sda1",
        "fingerprint": "abcdef1234567890",
        "raw": {"used_pct": 92},
    }
    event.update(overrides)
    return event


def _event_line(**overrides: Any) -> bytes:
    """Build a JSON-line bytes representation of a telemetryd event."""
    return json.dumps(_make_raw_event(**overrides)).encode("utf-8") + b"\n"


def _make_watcher(
    tmp_path: Path | None = None,
    queue: Any | None = None,
    shutdown: asyncio.Event | None = None,
) -> TelemetrydWatcher:
    """Create a watcher with mock dependencies."""
    q = queue or MagicMock()
    q.put_nowait = MagicMock(return_value=True)
    sd = shutdown or asyncio.Event()
    sp = (tmp_path / "telemetry.sock") if tmp_path else Path("/tmp/fake.sock")
    return TelemetrydWatcher(event_queue=q, shutdown=sd, socket_path=sp)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def watcher(tmp_path):
    return _make_watcher(tmp_path)


# ---------------------------------------------------------------------------
# is_telemetryd_available (module-level helper)
# ---------------------------------------------------------------------------


class TestIsTelemetrydAvailable:
    def test_socket_not_exists(self, tmp_path):
        assert is_telemetryd_available(tmp_path / "no.sock") is False

    @patch("elle.daemon.telemetry.telemetryd_watcher.socket.socket")
    def test_socket_connect_fails(self, mock_cls, tmp_path):
        sock_path = tmp_path / "test.sock"
        sock_path.touch()
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = OSError("refused")
        mock_cls.return_value = mock_sock
        assert is_telemetryd_available(sock_path) is False

    @patch("elle.daemon.telemetry.telemetryd_watcher.socket.socket")
    def test_socket_connect_ok(self, mock_cls, tmp_path):
        sock_path = tmp_path / "test.sock"
        sock_path.touch()
        mock_sock = MagicMock()
        mock_cls.return_value = mock_sock
        assert is_telemetryd_available(sock_path) is True
        mock_sock.close.assert_called_once()


# ---------------------------------------------------------------------------
# TelemetrydWatcher: init / properties
# ---------------------------------------------------------------------------


class TestWatcherProperties:
    def test_initial_state(self, watcher):
        assert watcher.running is False
        assert watcher.connected is False
        assert watcher.total_events == 0
        assert watcher.restart_count == 0

    def test_last_event_age_none(self, watcher):
        assert watcher.last_event_age_seconds is None

    def test_last_event_age_value(self, watcher):
        watcher._last_event_time = datetime.now(timezone.utc)
        age = watcher.last_event_age_seconds
        assert age is not None
        assert age >= 0

    def test_is_healthy_not_running(self, watcher):
        assert watcher.is_healthy is False

    def test_is_healthy_running_connected_no_events(self, watcher):
        watcher._running = True
        watcher._connected = True
        assert watcher.is_healthy is True

    def test_is_healthy_running_connected_recent_event(self, watcher):
        watcher._running = True
        watcher._connected = True
        watcher._last_event_time = datetime.now(timezone.utc)
        assert watcher.is_healthy is True

    def test_is_healthy_stale_events(self, watcher):
        watcher._running = True
        watcher._connected = True
        from datetime import timedelta

        watcher._last_event_time = datetime.now(timezone.utc) - timedelta(seconds=STALE_THRESHOLD + 10)
        assert watcher.is_healthy is False


# ---------------------------------------------------------------------------
# _convert_to_event
# ---------------------------------------------------------------------------


class TestConvertToEvent:
    def test_valid_event(self, watcher):
        raw = _make_raw_event()
        event = watcher._convert_to_event(raw)
        assert event is not None
        assert isinstance(event, TelemetryEvent)
        assert event.source == "journal"
        assert event.severity == "warning"
        assert event.category == "disk"
        assert event.message == "Disk usage exceeded 90%"
        assert event.raw == {"used_pct": 92}

    def test_zero_timestamp_uses_now(self, watcher):
        raw = _make_raw_event(ts=0)
        event = watcher._convert_to_event(raw)
        assert event is not None
        assert event.ts.tzinfo == timezone.utc

    def test_missing_fields_defaults(self, watcher):
        raw = {"message": "minimal"}
        event = watcher._convert_to_event(raw)
        assert event is not None
        assert event.source == "journal"
        assert event.severity == "info"
        assert event.category == "other"

    def test_invalid_source_defaults_to_journal(self, watcher):
        raw = _make_raw_event(source="UNKNOWN")
        event = watcher._convert_to_event(raw)
        assert event is not None
        assert event.source == "journal"

    def test_all_valid_sources(self, watcher):
        for src in ("journal", "kernel", "probe", "ebpf", "docker", "inotify"):
            raw = _make_raw_event(source=src)
            event = watcher._convert_to_event(raw)
            assert event is not None
            assert event.source == src

    def test_entity_passed_through(self, watcher):
        raw = _make_raw_event(entity="service:nginx")
        event = watcher._convert_to_event(raw)
        assert event is not None
        assert event.entity == "service:nginx"

    def test_conversion_failure_returns_none(self, watcher):
        # If an unexpected exception occurs, should return None
        with patch.object(TelemetryEvent, "__init__", side_effect=RuntimeError("bad")):
            result = watcher._convert_to_event({"message": "x"})
        assert result is None
        assert watcher._total_errors == 1


# ---------------------------------------------------------------------------
# _queue_event
# ---------------------------------------------------------------------------


class TestQueueEvent:
    def test_successful_queue(self, watcher):
        event = TelemetryEvent(source="journal", message="hi")
        watcher._queue_event(event)
        assert watcher._total_events == 1
        watcher._event_queue.put_nowait.assert_called_once_with(event)
        assert watcher._last_event_time is not None

    def test_queue_failure(self, watcher):
        watcher._event_queue.put_nowait.side_effect = RuntimeError("full")
        event = TelemetryEvent(source="journal", message="hi")
        watcher._queue_event(event)
        assert watcher._total_errors == 1
        assert watcher._total_events == 0


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


class TestStartStop:
    async def test_stop_clears_state(self, watcher):
        watcher._running = True
        watcher._connected = True
        watcher._writer = MagicMock()
        watcher._writer.close = MagicMock()
        watcher._writer.wait_closed = AsyncMock()
        watcher._reader = MagicMock()
        await watcher.stop()
        assert watcher.running is False

    async def test_start_not_available(self, watcher):
        with patch.object(watcher, "check_available", return_value=False):
            with pytest.raises(RuntimeError, match="not available"):
                await watcher.start()
        assert watcher.running is False

    async def test_stop_cancels_health_task(self, watcher):
        watcher._running = True

        # Create a real asyncio task that we can cancel
        async def _dummy_health():
            await asyncio.sleep(3600)

        health_task = asyncio.create_task(_dummy_health())
        watcher._health_task = health_task

        watcher._writer = None
        watcher._reader = None
        await watcher.stop()
        assert health_task.cancelled()
        assert watcher._health_task is None


# ---------------------------------------------------------------------------
# _connect / _disconnect
# ---------------------------------------------------------------------------


class TestConnectDisconnect:
    async def test_connect_no_socket(self, watcher):
        result = await watcher._connect()
        assert result is False

    async def test_connect_success(self, tmp_path):
        sock_path = tmp_path / "test.sock"
        sock_path.touch()
        watcher = _make_watcher(tmp_path)
        watcher._socket_path = sock_path
        mock_reader = MagicMock()
        mock_writer = MagicMock()
        with patch("asyncio.open_unix_connection", return_value=(mock_reader, mock_writer)):
            result = await watcher._connect()
        assert result is True
        assert watcher._connected is True
        assert watcher._reader is mock_reader
        assert watcher._writer is mock_writer

    async def test_connect_exception(self, tmp_path):
        sock_path = tmp_path / "test.sock"
        sock_path.touch()
        watcher = _make_watcher(tmp_path)
        watcher._socket_path = sock_path
        with patch("asyncio.open_unix_connection", side_effect=OSError("refused")):
            result = await watcher._connect()
        assert result is False

    async def test_disconnect(self, watcher):
        watcher._connected = True
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        watcher._writer = mock_writer
        watcher._reader = MagicMock()
        await watcher._disconnect()
        assert watcher._connected is False
        assert watcher._writer is None
        assert watcher._reader is None
        mock_writer.close.assert_called_once()

    async def test_disconnect_writer_error(self, watcher):
        watcher._connected = True
        mock_writer = MagicMock()
        mock_writer.close.side_effect = RuntimeError("close error")
        watcher._writer = mock_writer
        await watcher._disconnect()
        assert watcher._connected is False


# ---------------------------------------------------------------------------
# _check_health
# ---------------------------------------------------------------------------


class TestCheckHealth:
    async def test_no_reader(self, watcher):
        watcher._reader = None
        assert await watcher._check_health() is False

    async def test_reader_at_eof(self, watcher):
        mock_reader = MagicMock()
        mock_reader.at_eof.return_value = True
        watcher._reader = mock_reader
        assert await watcher._check_health() is False

    async def test_not_connected(self, watcher):
        watcher._reader = MagicMock()
        watcher._reader.at_eof.return_value = False
        watcher._connected = False
        assert await watcher._check_health() is False

    async def test_healthy_no_events(self, watcher):
        watcher._reader = MagicMock()
        watcher._reader.at_eof.return_value = False
        watcher._connected = True
        watcher._last_event_time = None
        assert await watcher._check_health() is True

    async def test_healthy_recent_event(self, watcher):
        watcher._reader = MagicMock()
        watcher._reader.at_eof.return_value = False
        watcher._connected = True
        watcher._last_event_time = datetime.now(timezone.utc)
        assert await watcher._check_health() is True


# ---------------------------------------------------------------------------
# _send_ping
# ---------------------------------------------------------------------------


class TestSendPing:
    async def test_no_writer(self, watcher):
        watcher._writer = None
        assert await watcher._send_ping() is False

    async def test_ping_success(self, watcher):
        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        watcher._writer = mock_writer
        assert await watcher._send_ping() is True
        mock_writer.write.assert_called_once_with(b"\n")

    async def test_ping_failure(self, watcher):
        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock(side_effect=OSError("broken pipe"))
        watcher._writer = mock_writer
        assert await watcher._send_ping() is False


# ---------------------------------------------------------------------------
# _attempt_recovery
# ---------------------------------------------------------------------------


class TestAttemptRecovery:
    async def test_max_restarts_exceeded(self, watcher):
        watcher._restart_count = MAX_RESTART_ATTEMPTS
        with patch.object(watcher, "_create_health_incident", new_callable=AsyncMock) as mock_inc:
            await watcher._attempt_recovery()
        mock_inc.assert_called_once()

    async def test_cooldown_resets_counter(self, watcher):
        from datetime import timedelta

        watcher._restart_count = 3
        watcher._last_restart_time = datetime.now(timezone.utc) - timedelta(seconds=RESTART_COOLDOWN + 10)
        watcher._writer = None
        watcher._reader = None
        with patch.object(watcher, "_disconnect", new_callable=AsyncMock):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_proc = MagicMock()
                mock_proc.wait = AsyncMock(return_value=0)
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with patch.object(watcher, "_connect", return_value=True):
                        await watcher._attempt_recovery()
        # Counter was reset to 0 then incremented to 1
        assert watcher._restart_count == 1


# ---------------------------------------------------------------------------
# _create_health_incident
# ---------------------------------------------------------------------------


class TestCreateHealthIncident:
    async def test_import_error(self, watcher):
        with patch("elle.daemon.telemetry.telemetryd_watcher.logger"):
            with patch.dict("sys.modules", {"elle.daemon.incidents.store": None}):
                # The import inside will raise ImportError
                await watcher._create_health_incident("title", "summary")

    async def test_success(self, watcher):
        mock_create = MagicMock()
        with patch("elle.daemon.incidents.store.create_incident_draft", mock_create):
            await watcher._create_health_incident("Test", "Test summary")
        mock_create.assert_called_once()


# ---------------------------------------------------------------------------
# check_available
# ---------------------------------------------------------------------------


class TestCheckAvailable:
    async def test_no_file(self, watcher):
        assert await watcher.check_available() is False

    async def test_not_a_socket(self, tmp_path):
        sock_path = tmp_path / "not_socket.txt"
        sock_path.touch()
        watcher = _make_watcher(tmp_path)
        watcher._socket_path = sock_path
        assert await watcher.check_available() is False
