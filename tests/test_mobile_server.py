from __future__ import annotations

"""Tests for ELLE Mobile Gateway server process management.

Covers:
- GatewayServer.__init__() with and without config
- GatewayServer.start() success and all error paths
- GatewayServer.stop() success, already stopped, no PID, SIGKILL fallback,
  ProcessLookupError, generic exception
- GatewayServer.restart() delegates to stop+start
- GatewayServer.get_status() PID file absent, invalid PID, stale PID,
  running with/without state file, uptime calculation
- run_gateway_foreground() with/without config
- format_status() for running and not-running states, with/without uptime
- ServerError exception
"""

import json
import signal
import ssl
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub out heavy elle.mobile dependencies before importing server module.
# The crypto module requires the 'cryptography' library which may not be
# installed in the test environment.
# ---------------------------------------------------------------------------
import elle.mobile

if "elle.mobile.crypto" not in sys.modules:
    _crypto_stub = MagicMock()
    sys.modules["elle.mobile.crypto"] = _crypto_stub
    elle.mobile.crypto = _crypto_stub

from elle.mobile.config import MobileGatewayConfig
from elle.mobile.models import GatewayStatus
from elle.mobile.server import (
    PID_FILE,
    STATE_FILE,
    GatewayServer,
    ServerError,
    format_status,
    run_gateway_foreground,
)

# =============================================================================
# Helpers
# =============================================================================

FAKE_CERT_PATHS = SimpleNamespace(
    ca_cert=Path("/fake/ca.crt"),
    ca_key=Path("/fake/ca.key"),
    server_cert=Path("/fake/server.crt"),
    server_key=Path("/fake/server.key"),
)


def _make_config(**overrides) -> MobileGatewayConfig:
    """Build a MobileGatewayConfig with sensible test defaults."""
    defaults = {
        "enabled": True,
        "bind_host": "127.0.0.1",
        "bind_port": 9999,
    }
    defaults.update(overrides)
    return MobileGatewayConfig(**defaults)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def config():
    """Standard test config."""
    return _make_config()


@pytest.fixture
def mock_store():
    """Mock MobileStore that returns safe defaults."""
    store = MagicMock()
    store.count_devices.return_value = 0
    store.list_active_elevations.return_value = []
    return store


@pytest.fixture
def mock_crypto():
    """Mock MobileCrypto that returns fake cert paths."""
    crypto = MagicMock()
    crypto.ensure_certificates.return_value = FAKE_CERT_PATHS
    return crypto


@pytest.fixture
def server(config, mock_store, mock_crypto):
    """GatewayServer with mocked dependencies."""
    with (
        patch("elle.mobile.server.MobileCrypto", return_value=mock_crypto),
        patch("elle.mobile.server.MobileStore", return_value=mock_store),
    ):
        srv = GatewayServer(config=config)
    # Replace internal references with our mocks for direct access
    srv.crypto = mock_crypto
    srv.store = mock_store
    return srv


# =============================================================================
# ServerError
# =============================================================================


class TestServerError:
    """Tests for the ServerError exception class."""

    def test_is_exception(self):
        """ServerError is a subclass of Exception."""
        assert issubclass(ServerError, Exception)

    def test_message(self):
        """ServerError carries the provided message."""
        err = ServerError("something broke")
        assert str(err) == "something broke"


# =============================================================================
# GatewayServer.__init__
# =============================================================================


class TestGatewayServerInit:
    """Tests for GatewayServer construction."""

    @patch("elle.mobile.server.MobileStore")
    @patch("elle.mobile.server.MobileCrypto")
    def test_init_with_explicit_config(self, mock_crypto_cls, mock_store_cls):
        """When a config is provided, it is used directly."""
        cfg = _make_config(bind_port=5555)
        srv = GatewayServer(config=cfg)
        assert srv.config is cfg
        mock_crypto_cls.assert_called_once_with(cfg)
        mock_store_cls.assert_called_once_with(cfg)

    @patch("elle.mobile.server.MobileStore")
    @patch("elle.mobile.server.MobileCrypto")
    @patch("elle.mobile.server.get_mobile_config")
    def test_init_without_config(self, mock_get_cfg, mock_crypto_cls, mock_store_cls):
        """When no config is provided, get_mobile_config() is called."""
        default_cfg = _make_config(bind_port=7777)
        mock_get_cfg.return_value = default_cfg
        srv = GatewayServer()
        mock_get_cfg.assert_called_once()
        assert srv.config is default_cfg


# =============================================================================
# GatewayServer.get_status
# =============================================================================


class TestGetStatus:
    """Tests for GatewayServer.get_status()."""

    def test_no_pid_file(self, server, tmp_path):
        """When PID file does not exist, returns not-running status."""
        fake_pid = tmp_path / "nonexistent.pid"
        with patch("elle.mobile.server.PID_FILE", fake_pid):
            status = server.get_status()
        assert not status.running
        assert status.pid is None

    def test_invalid_pid_content(self, server, tmp_path):
        """When PID file has non-integer content, returns not-running."""
        fake_pid = tmp_path / "bad.pid"
        fake_pid.write_text("not-a-number")
        with patch("elle.mobile.server.PID_FILE", fake_pid):
            status = server.get_status()
        assert not status.running

    def test_stale_pid_file(self, server, tmp_path):
        """When PID exists but process is not running, cleans up files."""
        fake_pid = tmp_path / "stale.pid"
        fake_state = tmp_path / "stale_state.json"
        fake_pid.write_text("99999")
        fake_state.write_text("{}")

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch("os.kill", side_effect=OSError("No such process")),
        ):
            status = server.get_status()

        assert not status.running
        assert not fake_pid.exists()
        assert not fake_state.exists()

    def test_stale_pid_no_state_file(self, server, tmp_path):
        """Stale PID cleanup works when state file does not exist."""
        fake_pid = tmp_path / "stale.pid"
        fake_pid.write_text("99999")
        fake_state = tmp_path / "no_state.json"

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch("os.kill", side_effect=OSError("No such process")),
        ):
            status = server.get_status()

        assert not status.running
        assert not fake_pid.exists()

    def test_running_with_state(self, server, tmp_path):
        """When process is running and state file exists, returns full status."""
        fake_pid = tmp_path / "running.pid"
        fake_state = tmp_path / "running_state.json"
        fake_pid.write_text("12345")

        started = datetime.now(timezone.utc) - timedelta(hours=2, minutes=15)
        state_data = {
            "pid": 12345,
            "started_at": started.isoformat(),
            "bind_host": "127.0.0.1",
            "bind_port": 9999,
        }
        fake_state.write_text(json.dumps(state_data))

        server.store.count_devices.return_value = 3
        server.store.list_active_elevations.return_value = [MagicMock(), MagicMock()]

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch("os.kill"),
        ):  # os.kill(pid, 0) succeeds
            status = server.get_status()

        assert status.running
        assert status.pid == 12345
        assert status.bind_host == "127.0.0.1"
        assert status.bind_port == 9999
        assert status.paired_devices == 3
        assert status.active_elevations == 2
        assert status.uptime_seconds is not None
        assert status.uptime_seconds > 0
        assert status.started_at == started

    def test_running_without_state_file(self, server, tmp_path):
        """When process is running but no state file, returns partial status."""
        fake_pid = tmp_path / "running.pid"
        fake_state = tmp_path / "missing_state.json"
        fake_pid.write_text("12345")

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch("os.kill"),
        ):
            status = server.get_status()

        assert status.running
        assert status.pid == 12345
        assert status.uptime_seconds is None
        assert status.started_at is None

    def test_running_with_corrupt_state(self, server, tmp_path):
        """When state file has invalid JSON, status still returns running."""
        fake_pid = tmp_path / "running.pid"
        fake_state = tmp_path / "corrupt_state.json"
        fake_pid.write_text("12345")
        fake_state.write_text("{bad json")

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch("os.kill"),
        ):
            status = server.get_status()

        assert status.running
        assert status.pid == 12345
        assert status.uptime_seconds is None

    def test_pid_file_read_os_error(self, server, tmp_path):
        """When PID file raises OSError on read, returns not-running."""
        fake_pid = tmp_path / "oserror.pid"
        fake_pid.write_text("12345")

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch.object(type(fake_pid), "read_text", side_effect=OSError("perm denied")),
        ):
            status = server.get_status()

        assert not status.running


# =============================================================================
# GatewayServer.start
# =============================================================================


class TestStart:
    """Tests for GatewayServer.start()."""

    def test_already_running(self, server):
        """Raises ServerError if gateway is already running."""
        running_status = GatewayStatus(running=True, pid=111)
        with patch.object(server, "get_status", return_value=running_status):
            with pytest.raises(ServerError, match="already running"):
                server.start()

    @patch("elle.mobile.server.time.sleep")
    @patch("elle.mobile.server.subprocess.Popen")
    def test_start_success(self, mock_popen, mock_sleep, server, tmp_path):
        """Successful start writes PID and state files, returns status."""
        fake_pid = tmp_path / "run" / "mobile_gateway.pid"
        fake_state = tmp_path / "lib" / "mobile_gateway_state.json"

        # Process mock
        proc = MagicMock()
        proc.pid = 42
        proc.poll.return_value = None  # still running
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="gateway", timeout=5)
        mock_popen.return_value = proc

        not_running = GatewayStatus(running=False)
        running = GatewayStatus(running=True, pid=42)

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch.object(server, "get_status", side_effect=[not_running, running]),
        ):
            result = server.start()

        assert result.running
        assert result.pid == 42
        assert fake_pid.read_text() == "42"

        # Verify state file contents
        state = json.loads(fake_state.read_text())
        assert state["pid"] == 42
        assert state["bind_host"] == "127.0.0.1"
        assert state["bind_port"] == 9999

        # Verify Popen was called with correct arguments
        args = mock_popen.call_args
        cmd = args[0][0]
        assert cmd[0] == sys.executable
        assert "-m" in cmd
        assert "uvicorn" in cmd
        assert "--host" in cmd
        assert "127.0.0.1" in cmd
        assert "--port" in cmd
        assert "9999" in cmd
        assert "--ssl-keyfile" in cmd
        assert str(FAKE_CERT_PATHS.server_key) in cmd
        assert "--ssl-certfile" in cmd
        assert str(FAKE_CERT_PATHS.server_cert) in cmd
        assert "--ssl-ca-certs" in cmd
        assert str(FAKE_CERT_PATHS.ca_cert) in cmd
        assert "--ssl-cert-reqs" in cmd
        assert str(ssl.CERT_OPTIONAL) in cmd

        # Verify subprocess options
        assert args[1]["stdout"] is not None  # subprocess.DEVNULL
        assert args[1]["start_new_session"] is True

        # Verify sleep was called
        mock_sleep.assert_called_once_with(0.5)

    @patch("elle.mobile.server.time.sleep")
    @patch("elle.mobile.server.subprocess.Popen")
    def test_start_process_exits_immediately(self, mock_popen, mock_sleep, server, tmp_path):
        """Returns None when process exits immediately during health check."""
        fake_pid = tmp_path / "run" / "mobile_gateway.pid"
        fake_state = tmp_path / "lib" / "mobile_gateway_state.json"

        proc = MagicMock()
        proc.pid = 55
        proc.poll.return_value = 1  # exited with code 1
        proc.returncode = 1
        mock_popen.return_value = proc

        not_running = GatewayStatus(running=False)

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch.object(server, "get_status", return_value=not_running),
        ):
            result = server.start()

        # Process exited with non-zero code during wait(timeout=5) health check
        assert result is None

    @patch("elle.mobile.server.time.sleep")
    @patch("elle.mobile.server.subprocess.Popen")
    def test_start_popen_raises(self, mock_popen, mock_sleep, server, tmp_path):
        """Raises ServerError when Popen itself throws."""
        fake_pid = tmp_path / "run" / "mobile_gateway.pid"
        fake_state = tmp_path / "lib" / "mobile_gateway_state.json"

        mock_popen.side_effect = OSError("cannot exec")

        not_running = GatewayStatus(running=False)

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch.object(server, "get_status", return_value=not_running),
        ):
            with pytest.raises(ServerError, match="Failed to start gateway"):
                server.start()

    @patch("elle.mobile.server.time.sleep")
    @patch("elle.mobile.server.subprocess.Popen")
    def test_start_cleanup_on_failure_files_exist(self, mock_popen, mock_sleep, server, tmp_path):
        """Process exits during health check — returns None, pre-existing files unchanged."""
        fake_pid = tmp_path / "run" / "mobile_gateway.pid"
        fake_state = tmp_path / "lib" / "mobile_gateway_state.json"

        # Pre-create the files so we can verify they aren't written to
        fake_pid.parent.mkdir(parents=True, exist_ok=True)
        fake_state.parent.mkdir(parents=True, exist_ok=True)
        fake_pid.write_text("old")
        fake_state.write_text("old")

        proc = MagicMock()
        proc.pid = 77
        proc.poll.return_value = 2
        proc.returncode = 2
        mock_popen.return_value = proc

        not_running = GatewayStatus(running=False)

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch.object(server, "get_status", return_value=not_running),
        ):
            result = server.start()

        # Process exited with non-zero during wait(timeout=5) — returns None early
        assert result is None

    @patch("elle.mobile.server.time.sleep")
    @patch("elle.mobile.server.subprocess.Popen")
    def test_start_cleanup_no_files(self, mock_popen, mock_sleep, server, tmp_path):
        """Cleanup branch: no error when PID/state files don't exist at cleanup."""
        fake_pid = tmp_path / "nonexistent" / "nope.pid"
        fake_state = tmp_path / "nonexistent" / "nope.json"

        mock_popen.side_effect = OSError("fail")

        not_running = GatewayStatus(running=False)

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch.object(server, "get_status", return_value=not_running),
        ):
            with pytest.raises(ServerError, match="Failed to start gateway"):
                server.start()


# =============================================================================
# GatewayServer.stop
# =============================================================================


class TestStop:
    """Tests for GatewayServer.stop()."""

    def test_stop_not_running(self, server):
        """Returns False when gateway is not running."""
        not_running = GatewayStatus(running=False)
        with patch.object(server, "get_status", return_value=not_running):
            result = server.stop()
        assert result is False

    def test_stop_no_pid(self, server):
        """Returns False when running but PID is None."""
        running_no_pid = GatewayStatus(running=True, pid=None)
        with patch.object(server, "get_status", return_value=running_no_pid):
            result = server.stop()
        assert result is False

    @patch("elle.mobile.server.time.sleep")
    @patch("os.kill")
    def test_stop_graceful(self, mock_kill, mock_sleep, server, tmp_path):
        """Process stops after SIGTERM within the polling window."""
        fake_pid = tmp_path / "stop.pid"
        fake_state = tmp_path / "stop_state.json"
        fake_pid.write_text("100")
        fake_state.write_text("{}")

        running = GatewayStatus(running=True, pid=100)

        # First call: SIGTERM succeeds; second call (signal 0): process gone
        kill_calls = 0

        def kill_side_effect(pid, sig):
            nonlocal kill_calls
            kill_calls += 1
            if sig == signal.SIGTERM:
                return  # SIGTERM accepted
            if sig == 0:
                raise OSError("No such process")  # process exited

        mock_kill.side_effect = kill_side_effect

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch.object(server, "get_status", return_value=running),
        ):
            result = server.stop()

        assert result is True
        assert not fake_pid.exists()
        assert not fake_state.exists()

    @patch("elle.mobile.server.time.sleep")
    @patch("os.kill")
    def test_stop_needs_sigkill(self, mock_kill, mock_sleep, server, tmp_path):
        """Process requires SIGKILL after SIGTERM timeout."""
        fake_pid = tmp_path / "stop.pid"
        fake_state = tmp_path / "stop_state.json"
        fake_pid.write_text("200")
        fake_state.write_text("{}")

        running = GatewayStatus(running=True, pid=200)

        # SIGTERM accepted; 50 probes all succeed (process still alive); then SIGKILL
        def kill_side_effect(pid, sig):
            if sig == signal.SIGKILL:
                return
            if sig == signal.SIGTERM:
                return
            if sig == 0:
                return  # process still alive

        mock_kill.side_effect = kill_side_effect

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch.object(server, "get_status", return_value=running),
        ):
            result = server.stop()

        assert result is True

        # Verify SIGKILL was sent
        kill_sigs = [c.args[1] for c in mock_kill.call_args_list]
        assert signal.SIGTERM in kill_sigs
        assert signal.SIGKILL in kill_sigs

        # 50 polling sleeps of 0.1
        assert mock_sleep.call_count == 50

    @patch("elle.mobile.server.time.sleep")
    @patch("os.kill")
    def test_stop_process_already_gone(self, mock_kill, mock_sleep, server, tmp_path):
        """ProcessLookupError on SIGTERM is handled gracefully."""
        fake_pid = tmp_path / "stop.pid"
        fake_state = tmp_path / "stop_state.json"
        fake_pid.write_text("300")
        fake_state.write_text("{}")

        running = GatewayStatus(running=True, pid=300)
        mock_kill.side_effect = ProcessLookupError("No such process")

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch.object(server, "get_status", return_value=running),
        ):
            result = server.stop()

        assert result is True
        # Cleanup still happens
        assert not fake_pid.exists()
        assert not fake_state.exists()

    @patch("elle.mobile.server.time.sleep")
    @patch("os.kill")
    def test_stop_unexpected_error(self, mock_kill, mock_sleep, server, tmp_path):
        """Generic exception during stop raises ServerError."""
        fake_pid = tmp_path / "stop.pid"
        fake_state = tmp_path / "stop_state.json"
        fake_pid.write_text("400")
        fake_state.write_text("{}")

        running = GatewayStatus(running=True, pid=400)
        mock_kill.side_effect = PermissionError("no permission")

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch.object(server, "get_status", return_value=running),
        ):
            with pytest.raises(ServerError, match="Failed to stop gateway"):
                server.stop()

        # Cleanup still happens in finally block
        assert not fake_pid.exists()
        assert not fake_state.exists()

    @patch("elle.mobile.server.time.sleep")
    @patch("os.kill")
    def test_stop_cleanup_no_files(self, mock_kill, mock_sleep, server, tmp_path):
        """Cleanup in finally block does not fail when files are already gone."""
        fake_pid = tmp_path / "gone.pid"
        fake_state = tmp_path / "gone_state.json"
        # Files do NOT exist on disk

        running = GatewayStatus(running=True, pid=500)
        mock_kill.side_effect = ProcessLookupError("already gone")

        with (
            patch("elle.mobile.server.PID_FILE", fake_pid),
            patch("elle.mobile.server.STATE_FILE", fake_state),
            patch.object(server, "get_status", return_value=running),
        ):
            result = server.stop()

        assert result is True


# =============================================================================
# GatewayServer.restart
# =============================================================================


class TestRestart:
    """Tests for GatewayServer.restart()."""

    @patch("elle.mobile.server.time.sleep")
    def test_restart_delegates(self, mock_sleep, server):
        """restart() calls stop(), sleeps, then start()."""
        expected_status = GatewayStatus(running=True, pid=88)

        with (
            patch.object(server, "stop", return_value=True) as mock_stop,
            patch.object(server, "start", return_value=expected_status) as mock_start,
        ):
            result = server.restart()

        mock_stop.assert_called_once()
        mock_sleep.assert_called_once_with(0.5)
        mock_start.assert_called_once()
        assert result.running
        assert result.pid == 88


# =============================================================================
# run_gateway_foreground
# =============================================================================


class TestRunGatewayForeground:
    """Tests for the run_gateway_foreground() function."""

    @patch("elle.mobile.server.MobileCrypto")
    @patch("elle.mobile.server.uvicorn", create=True)
    def test_with_explicit_config(self, mock_uvicorn_module, mock_crypto_cls):
        """When config is provided, it is used directly."""
        cfg = _make_config(bind_host="10.0.0.1", bind_port=4444)
        crypto_inst = MagicMock()
        crypto_inst.ensure_certificates.return_value = FAKE_CERT_PATHS
        mock_crypto_cls.return_value = crypto_inst

        # Patch uvicorn at the point of import inside run_gateway_foreground
        mock_uvicorn = MagicMock()
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            run_gateway_foreground(config=cfg)

        mock_uvicorn.run.assert_called_once()
        call_kwargs = mock_uvicorn.run.call_args[1]
        assert call_kwargs["host"] == "10.0.0.1"
        assert call_kwargs["port"] == 4444
        assert call_kwargs["factory"] is True
        assert call_kwargs["ssl_keyfile"] == str(FAKE_CERT_PATHS.server_key)
        assert call_kwargs["ssl_certfile"] == str(FAKE_CERT_PATHS.server_cert)
        assert call_kwargs["ssl_ca_certs"] == str(FAKE_CERT_PATHS.ca_cert)
        assert call_kwargs["ssl_cert_reqs"] == ssl.CERT_OPTIONAL

    @patch("elle.mobile.server.MobileCrypto")
    @patch("elle.mobile.server.get_mobile_config")
    def test_without_config(self, mock_get_cfg, mock_crypto_cls):
        """When no config is provided, get_mobile_config() is called."""
        cfg = _make_config()
        mock_get_cfg.return_value = cfg
        crypto_inst = MagicMock()
        crypto_inst.ensure_certificates.return_value = FAKE_CERT_PATHS
        mock_crypto_cls.return_value = crypto_inst

        mock_uvicorn = MagicMock()
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            run_gateway_foreground()

        mock_get_cfg.assert_called_once()
        mock_uvicorn.run.assert_called_once()


# =============================================================================
# format_status
# =============================================================================


class TestFormatStatus:
    """Tests for the format_status() function."""

    def test_not_running(self):
        """Not-running status returns short message."""
        status = GatewayStatus(running=False)
        output = format_status(status)
        assert output == "Mobile Gateway: Not running"

    def test_running_basic(self):
        """Running status includes PID, address, device and elevation counts."""
        status = GatewayStatus(
            running=True,
            pid=1234,
            bind_host="0.0.0.0",
            bind_port=8378,
            paired_devices=5,
            active_elevations=2,
        )
        output = format_status(status)
        assert "Running" in output
        assert "1234" in output
        assert "0.0.0.0:8378" in output
        assert "5" in output
        assert "2" in output

    def test_running_with_uptime(self):
        """Uptime is formatted as hours and minutes."""
        status = GatewayStatus(
            running=True,
            pid=1234,
            bind_host="0.0.0.0",
            bind_port=8378,
            uptime_seconds=7500.0,  # 2h 5m
        )
        output = format_status(status)
        assert "2h 5m" in output

    def test_running_with_started_at(self):
        """Started timestamp is included when present."""
        started = datetime(2025, 6, 15, 10, 30, 0)
        status = GatewayStatus(
            running=True,
            pid=1234,
            bind_host="0.0.0.0",
            bind_port=8378,
            started_at=started,
        )
        output = format_status(status)
        assert "2025-06-15" in output
        assert "Started" in output

    def test_running_without_uptime(self):
        """When uptime_seconds is None, uptime line is omitted."""
        status = GatewayStatus(
            running=True,
            pid=1234,
            bind_host="0.0.0.0",
            bind_port=8378,
        )
        output = format_status(status)
        assert "Uptime" not in output

    def test_running_without_started_at(self):
        """When started_at is None, started line is omitted."""
        status = GatewayStatus(
            running=True,
            pid=1234,
            bind_host="0.0.0.0",
            bind_port=8378,
        )
        output = format_status(status)
        assert "Started" not in output

    def test_running_full_status(self):
        """Full status with all fields populated."""
        started = datetime(2025, 1, 1, 0, 0, 0)
        status = GatewayStatus(
            running=True,
            pid=9999,
            bind_host="192.168.1.100",
            bind_port=8378,
            paired_devices=10,
            active_elevations=3,
            uptime_seconds=3661.0,  # 1h 1m
            started_at=started,
        )
        output = format_status(status)
        lines = output.strip().split("\n")
        assert len(lines) == 7
        assert "Running" in lines[0]
        assert "9999" in lines[1]
        assert "192.168.1.100:8378" in lines[2]
        assert "10" in lines[3]
        assert "3" in lines[4]
        assert "1h 1m" in lines[5]
        assert "2025-01-01" in lines[6]

    def test_uptime_zero(self):
        """Zero uptime is formatted as 0h 0m."""
        status = GatewayStatus(
            running=True,
            pid=1,
            bind_host="0.0.0.0",
            bind_port=8378,
            uptime_seconds=0.0,
        )
        output = format_status(status)
        assert "0h 0m" in output

    def test_uptime_fractional_minutes(self):
        """Fractional minutes are truncated, not rounded."""
        status = GatewayStatus(
            running=True,
            pid=1,
            bind_host="0.0.0.0",
            bind_port=8378,
            uptime_seconds=3599.0,  # 0h 59m
        )
        output = format_status(status)
        assert "0h 59m" in output


# =============================================================================
# Module-level constants
# =============================================================================


class TestModuleConstants:
    """Tests for module-level constants PID_FILE and STATE_FILE."""

    def test_pid_file_path(self):
        """PID_FILE is at the expected location."""
        assert Path("/var/run/elle/mobile_gateway.pid") == PID_FILE

    def test_state_file_path(self):
        """STATE_FILE is at the expected location."""
        assert Path("/var/lib/elle/mobile_gateway_state.json") == STATE_FILE
