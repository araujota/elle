"""Comprehensive tests for elle.cli.daemon_commands to increase statement coverage.

Targets: DaemonHealth, DaemonStatus, AutonomousAction models,
DaemonStatusDisplay.render (running/not-running, all sections),
DaemonExplainer.explain,
handle_daemon_command (dispatch all subcommands),
_handle_status, _handle_explain, _handle_start, _handle_stop,
_handle_restart, _handle_logs, _handle_actions,
_handle_help, _get_daemon_status, _check_daemon_health,
_get_autonomous_actions, handle_daemon_command_sync.
"""
from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elle.cli.daemon_commands import (
    AutonomousAction,
    DaemonExplainer,
    DaemonHealth,
    DaemonStatus,
    DaemonStatusDisplay,
    _check_daemon_health,
    _get_autonomous_actions,
    _get_daemon_status,
    _handle_actions,
    _handle_help,
    _handle_logs,
    _handle_restart,
    _handle_start,
    _handle_stop,
    handle_daemon_command,
    handle_daemon_command_sync,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestModels:
    def test_daemon_health_defaults(self) -> None:
        h = DaemonHealth(running=False)
        assert h.pid is None
        assert h.uptime_seconds == 0
        assert h.api_reachable is False
        assert h.last_error is None

    def test_daemon_status_defaults(self) -> None:
        h = DaemonHealth(running=True, pid=1234)
        s = DaemonStatus(health=h)
        assert s.version == "unknown"
        assert s.events_processed_total == 0
        assert s.incidents_open == 0
        assert s.active_watchers == ()

    def test_autonomous_action(self) -> None:
        a = AutonomousAction(
            action_id="a1",
            capability_name="service.restart",
            incident_id="inc-1",
            triggered_by="daemon",
            input_summary="restarted nginx",
            success=True,
            executed_at=datetime.now(timezone.utc),
            duration_ms=150,
        )
        assert a.success is True
        assert a.duration_ms == 150


# ---------------------------------------------------------------------------
# DaemonStatusDisplay
# ---------------------------------------------------------------------------


class TestDaemonStatusDisplay:
    def _display(self) -> DaemonStatusDisplay:
        return DaemonStatusDisplay()

    def test_render_not_running(self) -> None:
        d = self._display()
        health = DaemonHealth(running=False, last_error="service not found")
        status = DaemonStatus(health=health)
        output = d.render(status)
        assert "NOT RUNNING" in output
        assert "service not found" in output
        assert "daemon start" in output

    def test_render_not_running_no_error(self) -> None:
        d = self._display()
        health = DaemonHealth(running=False)
        status = DaemonStatus(health=health)
        output = d.render(status)
        assert "NOT RUNNING" in output

    def test_render_running_full(self) -> None:
        d = self._display()
        now = datetime.now(timezone.utc)
        health = DaemonHealth(
            running=True,
            pid=12345,
            uptime_seconds=3700,
            api_reachable=True,
        )
        status = DaemonStatus(
            health=health,
            version="0.1.0",
            events_processed_total=1234,
            events_per_minute=2.5,
            incidents_open=3,
            incidents_total=50,
            last_incident_at=now - timedelta(minutes=5),
            active_watchers=("journal", "kernel"),
            watcher_status={"journal": "healthy", "kernel": "healthy"},
            last_autonomous_action="service.restart",
            last_autonomous_action_at=now - timedelta(hours=2),
            memory_mb=42.5,
            cpu_percent=1.2,
        )
        output = d.render(status)
        assert "RUNNING" in output
        assert "12345" in output
        assert "1h" in output  # uptime
        assert "0.1.0" in output
        assert "reachable" in output
        assert "42.5" in output
        assert "1,234" in output  # events formatted
        assert "INCIDENTS" in output
        assert "journal" in output
        assert "AUTONOMOUS" in output

    def test_render_running_no_watchers(self) -> None:
        d = self._display()
        health = DaemonHealth(running=True, pid=1, uptime_seconds=30)
        status = DaemonStatus(
            health=health,
            active_watchers=(),
        )
        output = d.render(status)
        assert "(none)" in output

    def test_format_uptime_seconds(self) -> None:
        d = self._display()
        assert d._format_uptime(30) == "30s"

    def test_format_uptime_minutes(self) -> None:
        d = self._display()
        assert d._format_uptime(125) == "2m 5s"

    def test_format_uptime_hours(self) -> None:
        d = self._display()
        assert d._format_uptime(7200) == "2h 0m"

    def test_format_time_ago_seconds(self) -> None:
        d = self._display()
        dt = datetime.now(timezone.utc) - timedelta(seconds=30)
        result = d._format_time_ago(dt)
        assert "s ago" in result

    def test_format_time_ago_minutes(self) -> None:
        d = self._display()
        dt = datetime.now(timezone.utc) - timedelta(minutes=5)
        result = d._format_time_ago(dt)
        assert "m ago" in result

    def test_format_time_ago_hours(self) -> None:
        d = self._display()
        dt = datetime.now(timezone.utc) - timedelta(hours=3)
        result = d._format_time_ago(dt)
        assert "h ago" in result

    def test_format_time_ago_days(self) -> None:
        d = self._display()
        dt = datetime.now(timezone.utc) - timedelta(days=2)
        result = d._format_time_ago(dt)
        assert "d ago" in result

    def test_format_time_ago_naive(self) -> None:
        d = self._display()
        dt = datetime.now() - timedelta(minutes=10)
        result = d._format_time_ago(dt)
        assert "m ago" in result


# ---------------------------------------------------------------------------
# DaemonExplainer
# ---------------------------------------------------------------------------


class TestDaemonExplainer:
    def test_explain(self) -> None:
        e = DaemonExplainer()
        output = e.explain()
        assert "ELLE DAEMON" in output
        assert "Journal Watcher" in output
        assert "Kernel Watcher" in output
        assert "Docker Watcher" in output
        assert "AUTONOMOUS ACTIONS" in output


# ---------------------------------------------------------------------------
# _handle_help
# ---------------------------------------------------------------------------


class TestHandleHelp:
    def test_help_text(self) -> None:
        output = _handle_help("")
        assert "daemon" in output.lower()
        assert "status" in output
        assert "explain" in output


# ---------------------------------------------------------------------------
# _handle_start
# ---------------------------------------------------------------------------


class TestHandleStart:
    def test_user_level_success(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("elle.cli.daemon_commands.subprocess.run", return_value=mock_result):
            output = _handle_start("")
        assert "started successfully" in output

    def test_user_level_fail_system_success(self) -> None:
        call_count = 0

        def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:
                r.returncode = 1
                r.stderr = "not found"
                r.stdout = ""
            else:
                r.returncode = 0
            return r

        with patch("elle.cli.daemon_commands.subprocess.run", side_effect=mock_run):
            output = _handle_start("")
        assert "system-level" in output

    def test_both_fail(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error msg"
        mock_result.stdout = ""
        with patch("elle.cli.daemon_commands.subprocess.run", return_value=mock_result):
            output = _handle_start("")
        assert "Failed" in output

    def test_timeout(self) -> None:
        with patch(
            "elle.cli.daemon_commands.subprocess.run",
            side_effect=subprocess.TimeoutExpired("systemctl", 10),
        ):
            output = _handle_start("")
        assert "Timeout" in output

    def test_file_not_found(self) -> None:
        with patch(
            "elle.cli.daemon_commands.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            output = _handle_start("")
        assert "systemctl not found" in output

    def test_generic_exception(self) -> None:
        with patch(
            "elle.cli.daemon_commands.subprocess.run",
            side_effect=RuntimeError("weird"),
        ):
            output = _handle_start("")
        assert "Error" in output


# ---------------------------------------------------------------------------
# _handle_stop
# ---------------------------------------------------------------------------


class TestHandleStop:
    def test_user_level_success(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("elle.cli.daemon_commands.subprocess.run", return_value=mock_result):
            output = _handle_stop("")
        assert "stopped" in output.lower()

    def test_user_fail_system_success(self) -> None:
        call_count = 0

        def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:
                r.returncode = 1
                r.stderr = ""
                r.stdout = ""
            else:
                r.returncode = 0
            return r

        with patch("elle.cli.daemon_commands.subprocess.run", side_effect=mock_run):
            output = _handle_stop("")
        assert "system-level" in output

    def test_both_fail(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "err"
        mock_result.stdout = ""
        with patch("elle.cli.daemon_commands.subprocess.run", return_value=mock_result):
            output = _handle_stop("")
        assert "Failed" in output

    def test_timeout(self) -> None:
        with patch(
            "elle.cli.daemon_commands.subprocess.run",
            side_effect=subprocess.TimeoutExpired("systemctl", 10),
        ):
            output = _handle_stop("")
        assert "Timeout" in output

    def test_file_not_found(self) -> None:
        with patch(
            "elle.cli.daemon_commands.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            output = _handle_stop("")
        assert "systemctl not found" in output

    def test_generic_exception(self) -> None:
        with patch(
            "elle.cli.daemon_commands.subprocess.run",
            side_effect=RuntimeError("weird"),
        ):
            output = _handle_stop("")
        assert "Error" in output


# ---------------------------------------------------------------------------
# _handle_restart
# ---------------------------------------------------------------------------


class TestHandleRestart:
    def test_user_level_success(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("elle.cli.daemon_commands.subprocess.run", return_value=mock_result):
            output = _handle_restart("")
        assert "restarted" in output.lower()

    def test_user_fail_system_success(self) -> None:
        call_count = 0

        def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:
                r.returncode = 1
                r.stderr = ""
                r.stdout = ""
            else:
                r.returncode = 0
            return r

        with patch("elle.cli.daemon_commands.subprocess.run", side_effect=mock_run):
            output = _handle_restart("")
        assert "system-level" in output

    def test_both_fail(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "err"
        mock_result.stdout = ""
        with patch("elle.cli.daemon_commands.subprocess.run", return_value=mock_result):
            output = _handle_restart("")
        assert "Failed" in output

    def test_timeout(self) -> None:
        with patch(
            "elle.cli.daemon_commands.subprocess.run",
            side_effect=subprocess.TimeoutExpired("systemctl", 15),
        ):
            output = _handle_restart("")
        assert "Timeout" in output

    def test_file_not_found(self) -> None:
        with patch(
            "elle.cli.daemon_commands.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            output = _handle_restart("")
        assert "systemctl not found" in output

    def test_generic_exception(self) -> None:
        with patch(
            "elle.cli.daemon_commands.subprocess.run",
            side_effect=RuntimeError("weird"),
        ):
            output = _handle_restart("")
        assert "Error" in output


# ---------------------------------------------------------------------------
# _handle_logs
# ---------------------------------------------------------------------------


class TestHandleLogs:
    def test_user_logs_success(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Jan 01 log line here"
        with patch("elle.cli.daemon_commands.subprocess.run", return_value=mock_result):
            output = _handle_logs("")
        assert "log line" in output

    def test_user_empty_system_success(self) -> None:
        call_count = 0

        def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:
                r.returncode = 0
                r.stdout = ""  # empty
            else:
                r.returncode = 0
                r.stdout = "system log line"
            return r

        with patch("elle.cli.daemon_commands.subprocess.run", side_effect=mock_run):
            output = _handle_logs("")
        assert "system log line" in output

    def test_no_logs_found(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("elle.cli.daemon_commands.subprocess.run", return_value=mock_result):
            output = _handle_logs("")
        assert "No daemon logs" in output

    def test_timeout(self) -> None:
        with patch(
            "elle.cli.daemon_commands.subprocess.run",
            side_effect=subprocess.TimeoutExpired("journalctl", 10),
        ):
            output = _handle_logs("")
        assert "Timeout" in output

    def test_file_not_found(self) -> None:
        with patch(
            "elle.cli.daemon_commands.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            output = _handle_logs("")
        assert "journalctl not found" in output

    def test_generic_exception(self) -> None:
        with patch(
            "elle.cli.daemon_commands.subprocess.run",
            side_effect=RuntimeError("err"),
        ):
            output = _handle_logs("")
        assert "Error" in output


# ---------------------------------------------------------------------------
# _handle_actions
# ---------------------------------------------------------------------------


class TestHandleActions:
    @pytest.mark.asyncio
    async def test_no_actions(self) -> None:
        with patch(
            "elle.cli.daemon_commands._get_autonomous_actions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            output = await _handle_actions("")
        assert "No autonomous actions" in output

    @pytest.mark.asyncio
    async def test_with_actions(self) -> None:
        actions = [
            AutonomousAction(
                action_id="a1",
                capability_name="service.restart",
                incident_id="inc-abc",
                triggered_by="daemon",
                input_summary="restarted nginx",
                success=True,
                executed_at=datetime.now(timezone.utc),
            ),
            AutonomousAction(
                action_id="a2",
                capability_name="file.write",
                incident_id=None,
                triggered_by="user",
                input_summary="wrote config",
                success=False,
                executed_at=datetime.now(timezone.utc),
            ),
        ]
        with patch(
            "elle.cli.daemon_commands._get_autonomous_actions",
            new_callable=AsyncMock,
            return_value=actions,
        ):
            output = await _handle_actions("")
        assert "[OK]" in output
        assert "[FAIL]" in output
        assert "service.restart" in output
        assert "inc-abc" in output

    @pytest.mark.asyncio
    async def test_with_limit_arg(self) -> None:
        with patch(
            "elle.cli.daemon_commands._get_autonomous_actions",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_fn:
            await _handle_actions("5")
        mock_fn.assert_called_once_with(5)

    @pytest.mark.asyncio
    async def test_invalid_limit(self) -> None:
        output = await _handle_actions("abc")
        assert "Invalid" in output

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        with patch(
            "elle.cli.daemon_commands._get_autonomous_actions",
            new_callable=AsyncMock,
            side_effect=RuntimeError("fail"),
        ):
            output = await _handle_actions("")
        assert "Error" in output


# ---------------------------------------------------------------------------
# handle_daemon_command (dispatch)
# ---------------------------------------------------------------------------


class TestHandleDaemonCommand:
    @pytest.mark.asyncio
    async def test_empty_args_status(self) -> None:
        with patch(
            "elle.cli.daemon_commands._handle_status",
            new_callable=AsyncMock,
            return_value="status output",
        ):
            output = await handle_daemon_command("")
        assert output == "status output"

    @pytest.mark.asyncio
    async def test_status_explicit(self) -> None:
        with patch(
            "elle.cli.daemon_commands._handle_status",
            new_callable=AsyncMock,
            return_value="status output",
        ):
            output = await handle_daemon_command("status")
        assert output == "status output"

    @pytest.mark.asyncio
    async def test_explain(self) -> None:
        output = await handle_daemon_command("explain")
        assert "ELLE DAEMON" in output

    @pytest.mark.asyncio
    async def test_start(self) -> None:
        with patch(
            "elle.cli.daemon_commands._handle_start",
            return_value="started",
        ):
            output = await handle_daemon_command("start")
        assert output == "started"

    @pytest.mark.asyncio
    async def test_stop(self) -> None:
        with patch(
            "elle.cli.daemon_commands._handle_stop",
            return_value="stopped",
        ):
            output = await handle_daemon_command("stop")
        assert output == "stopped"

    @pytest.mark.asyncio
    async def test_restart(self) -> None:
        with patch(
            "elle.cli.daemon_commands._handle_restart",
            return_value="restarted",
        ):
            output = await handle_daemon_command("restart")
        assert output == "restarted"

    @pytest.mark.asyncio
    async def test_logs(self) -> None:
        with patch(
            "elle.cli.daemon_commands._handle_logs",
            return_value="log output",
        ):
            output = await handle_daemon_command("logs")
        assert output == "log output"

    @pytest.mark.asyncio
    async def test_help(self) -> None:
        output = await handle_daemon_command("help")
        assert "daemon" in output.lower()

    @pytest.mark.asyncio
    async def test_actions(self) -> None:
        with patch(
            "elle.cli.daemon_commands._handle_actions",
            new_callable=AsyncMock,
            return_value="actions output",
        ):
            output = await handle_daemon_command("actions 5")
        assert output == "actions output"

    @pytest.mark.asyncio
    async def test_unknown_subcommand(self) -> None:
        output = await handle_daemon_command("zzz_unknown")
        assert "Unknown daemon subcommand" in output


# ---------------------------------------------------------------------------
# _get_daemon_status
# ---------------------------------------------------------------------------


class TestGetDaemonStatus:
    @pytest.mark.asyncio
    async def test_daemon_api_success(self) -> None:
        mock_client = MagicMock()
        mock_client.is_daemon_available = AsyncMock(return_value=True)

        mock_http = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "pid": 999,
            "uptime_seconds": 600,
            "version": "1.0",
            "events_processed_total": 100,
            "events_per_minute": 5.0,
            "incidents_open": 2,
            "incidents_total": 10,
            "active_watchers": ["journal"],
            "watcher_status": {"journal": "ok"},
            "last_autonomous_action": "test",
            "memory_mb": 30.0,
            "cpu_percent": 0.5,
        }
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_client._get_client = AsyncMock(return_value=mock_http)

        client_mod = MagicMock()
        client_mod.get_daemon_client.return_value = mock_client

        with patch.dict("sys.modules", {"elle.cli.daemon_client": client_mod}):
            status = await _get_daemon_status()
        assert status.health.running is True
        assert status.health.pid == 999
        assert status.version == "1.0"

    @pytest.mark.asyncio
    async def test_daemon_api_not_available(self) -> None:
        mock_client = MagicMock()
        mock_client.is_daemon_available = AsyncMock(return_value=False)
        mock_client._get_client = AsyncMock()

        client_mod = MagicMock()
        client_mod.get_daemon_client.return_value = mock_client

        with (
            patch.dict("sys.modules", {"elle.cli.daemon_client": client_mod}),
            patch(
                "elle.cli.daemon_commands._check_daemon_health",
                new_callable=AsyncMock,
                return_value=DaemonHealth(running=False),
            ),
        ):
            status = await _get_daemon_status()
        assert status.health.running is False

    @pytest.mark.asyncio
    async def test_daemon_api_exception(self) -> None:
        client_mod = MagicMock()
        client_mod.get_daemon_client.side_effect = Exception("nope")

        with (
            patch.dict("sys.modules", {"elle.cli.daemon_client": client_mod}),
            patch(
                "elle.cli.daemon_commands._check_daemon_health",
                new_callable=AsyncMock,
                return_value=DaemonHealth(running=False),
            ),
        ):
            status = await _get_daemon_status()
        assert status.health.running is False

    @pytest.mark.asyncio
    async def test_daemon_api_non_200(self) -> None:
        mock_client = MagicMock()
        mock_client.is_daemon_available = AsyncMock(return_value=True)

        mock_http = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_client._get_client = AsyncMock(return_value=mock_http)

        client_mod = MagicMock()
        client_mod.get_daemon_client.return_value = mock_client

        with (
            patch.dict("sys.modules", {"elle.cli.daemon_client": client_mod}),
            patch(
                "elle.cli.daemon_commands._check_daemon_health",
                new_callable=AsyncMock,
                return_value=DaemonHealth(running=False),
            ),
        ):
            status = await _get_daemon_status()
        assert status.health.running is False


# ---------------------------------------------------------------------------
# _check_daemon_health
# ---------------------------------------------------------------------------


class TestCheckDaemonHealth:
    @pytest.mark.asyncio
    async def test_user_service_active(self) -> None:
        call_count = 0

        def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:
                r.stdout = "active"
                r.returncode = 0
            else:
                r.stdout = "1234"
                r.returncode = 0
            return r

        with patch("elle.cli.daemon_commands.subprocess.run", side_effect=mock_run):
            health = await _check_daemon_health()
        assert health.running is True
        assert health.pid == 1234

    @pytest.mark.asyncio
    async def test_system_service_active(self) -> None:
        call_count = 0

        def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:
                r.stdout = "inactive"
                r.returncode = 0
            elif call_count == 2:
                r.stdout = "active"
                r.returncode = 0
            else:
                r.stdout = "5678"
                r.returncode = 0
            return r

        with patch("elle.cli.daemon_commands.subprocess.run", side_effect=mock_run):
            health = await _check_daemon_health()
        assert health.running is True
        assert health.pid == 5678

    @pytest.mark.asyncio
    async def test_not_running(self) -> None:
        mock_result = MagicMock()
        mock_result.stdout = "inactive"
        mock_result.stderr = "not found"
        mock_result.returncode = 3

        with patch("elle.cli.daemon_commands.subprocess.run", return_value=mock_result):
            health = await _check_daemon_health()
        assert health.running is False

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        with patch(
            "elle.cli.daemon_commands.subprocess.run",
            side_effect=RuntimeError("oops"),
        ):
            health = await _check_daemon_health()
        assert health.running is False
        assert "oops" in (health.last_error or "")


# ---------------------------------------------------------------------------
# _get_autonomous_actions
# ---------------------------------------------------------------------------


class TestGetAutonomousActions:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        mock_client = MagicMock()
        mock_http = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "actions": [
                {
                    "action_id": "a1",
                    "capability_name": "test",
                    "triggered_by": "daemon",
                    "input_summary": "summary",
                    "success": True,
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                }
            ]
        }
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_client._get_client = AsyncMock(return_value=mock_http)

        client_mod = MagicMock()
        client_mod.get_daemon_client.return_value = mock_client

        with patch.dict("sys.modules", {"elle.cli.daemon_client": client_mod}):
            actions = await _get_autonomous_actions(5)
        assert len(actions) == 1
        assert actions[0].capability_name == "test"

    @pytest.mark.asyncio
    async def test_non_200(self) -> None:
        mock_client = MagicMock()
        mock_http = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_client._get_client = AsyncMock(return_value=mock_http)

        client_mod = MagicMock()
        client_mod.get_daemon_client.return_value = mock_client

        with patch.dict("sys.modules", {"elle.cli.daemon_client": client_mod}):
            actions = await _get_autonomous_actions()
        assert actions == []

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        client_mod = MagicMock()
        client_mod.get_daemon_client.side_effect = Exception("fail")

        with patch.dict("sys.modules", {"elle.cli.daemon_client": client_mod}):
            actions = await _get_autonomous_actions()
        assert actions == []


# ---------------------------------------------------------------------------
# handle_daemon_command_sync
# ---------------------------------------------------------------------------


class TestHandleDaemonCommandSync:
    def test_sync_wrapper(self) -> None:
        with patch(
            "elle.cli.daemon_commands.handle_daemon_command",
            new_callable=AsyncMock,
            return_value="sync result",
        ):
            result = handle_daemon_command_sync("help")
        assert result == "sync result"
