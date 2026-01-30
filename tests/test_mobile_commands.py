from __future__ import annotations

"""Tests for mobile_commands.py - mobile gateway REPL commands."""

import pytest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _make_session():
    """Return a minimal real session."""
    from elle.common.session import create_session
    return create_session()


# ---------------------------------------------------------------------------
# Colors dataclass
# ---------------------------------------------------------------------------


class TestColors:
    def test_has_reset(self):
        from elle.cli.mobile_commands import Colors
        assert Colors.RESET == "\033[0m"

    def test_has_bold(self):
        from elle.cli.mobile_commands import Colors
        assert "\033[1m" == Colors.BOLD


# ---------------------------------------------------------------------------
# is_mobile_command
# ---------------------------------------------------------------------------


class TestIsMobileCommand:
    def test_slash_mobile(self):
        from elle.cli.mobile_commands import is_mobile_command
        assert is_mobile_command("/mobile") is True

    def test_slash_mobile_up(self):
        from elle.cli.mobile_commands import is_mobile_command
        assert is_mobile_command("/mobile up") is True

    def test_elle_mobile(self):
        from elle.cli.mobile_commands import is_mobile_command
        assert is_mobile_command("elle mobile status") is True

    def test_unrelated(self):
        from elle.cli.mobile_commands import is_mobile_command
        assert is_mobile_command("hello world") is False

    def test_case_insensitive(self):
        from elle.cli.mobile_commands import is_mobile_command
        assert is_mobile_command("/Mobile UP") is True


# ---------------------------------------------------------------------------
# handle_mobile_command - routing
# ---------------------------------------------------------------------------


class TestHandleMobileCommandRouting:
    def test_no_subcommand_shows_help(self):
        from elle.cli.mobile_commands import handle_mobile_command
        output, success = handle_mobile_command("/mobile", _make_session())
        assert success is True
        assert "Mobile Gateway" in output

    def test_help_subcommand(self):
        from elle.cli.mobile_commands import handle_mobile_command
        output, success = handle_mobile_command("/mobile help", _make_session())
        assert success is True
        assert "Mobile Gateway" in output

    def test_unknown_subcommand(self):
        from elle.cli.mobile_commands import handle_mobile_command
        output, success = handle_mobile_command("/mobile foobar", _make_session())
        assert success is False
        assert "Unknown subcommand" in output


# ---------------------------------------------------------------------------
# _mobile_help
# ---------------------------------------------------------------------------


class TestMobileHelp:
    def test_contains_expected_commands(self):
        from elle.cli.mobile_commands import _mobile_help
        help_text = _mobile_help()
        assert "/mobile up" in help_text
        assert "/mobile down" in help_text
        assert "/mobile status" in help_text
        assert "/mobile devices" in help_text
        assert "/mobile revoke" in help_text
        assert "/mobile approve" in help_text
        assert "/mobile audit" in help_text


# ---------------------------------------------------------------------------
# _mobile_down
# ---------------------------------------------------------------------------


class TestMobileDown:
    @patch("elle.cli.mobile_commands.GatewayServer", create=True)
    def test_stop_success(self, _mock):
        from elle.cli.mobile_commands import _mobile_down
        # We need to patch at import-time inside the function
        with patch.dict("sys.modules", {"elle.mobile.server": MagicMock()}):
            with patch("elle.cli.mobile_commands._mobile_down") as mock_fn:
                mock_fn.return_value = ("\nMobile gateway stopped\n", True)
                output, success = mock_fn(_make_session())
                assert success is True

    def test_stop_gateway_stopped(self):
        from elle.cli.mobile_commands import _mobile_down
        mock_server = MagicMock()
        mock_server.return_value.stop.return_value = True
        mock_module = MagicMock()
        mock_module.GatewayServer = mock_server
        with patch.dict("sys.modules", {"elle.mobile.server": mock_module}):
            output, success = _mobile_down(_make_session())
            assert success is True
            assert "stopped" in output

    def test_stop_gateway_not_running(self):
        from elle.cli.mobile_commands import _mobile_down
        mock_server = MagicMock()
        mock_server.return_value.stop.return_value = False
        mock_module = MagicMock()
        mock_module.GatewayServer = mock_server
        with patch.dict("sys.modules", {"elle.mobile.server": mock_module}):
            output, success = _mobile_down(_make_session())
            assert success is True
            assert "not running" in output

    def test_stop_exception(self):
        from elle.cli.mobile_commands import _mobile_down
        mock_server = MagicMock()
        mock_server.return_value.stop.side_effect = RuntimeError("oops")
        mock_module = MagicMock()
        mock_module.GatewayServer = mock_server
        with patch.dict("sys.modules", {"elle.mobile.server": mock_module}):
            output, success = _mobile_down(_make_session())
            assert success is False
            assert "Error" in output


# ---------------------------------------------------------------------------
# _mobile_status
# ---------------------------------------------------------------------------


class TestMobileStatus:
    def test_status_running(self):
        from elle.cli.mobile_commands import _mobile_status
        status_obj = SimpleNamespace(
            running=True,
            pid=12345,
            bind_host="0.0.0.0",
            bind_port=8379,
            paired_devices=2,
            active_elevations=1,
            uptime_seconds=7200.0,
            started_at=datetime(2024, 1, 1, 12, 0),
        )
        mock_server = MagicMock()
        mock_server.return_value.get_status.return_value = status_obj
        mock_module = MagicMock()
        mock_module.GatewayServer = mock_server
        with patch.dict("sys.modules", {"elle.mobile.server": mock_module}):
            output, success = _mobile_status(_make_session())
            assert success is True
            assert "Running" in output
            assert "12345" in output

    def test_status_not_running(self):
        from elle.cli.mobile_commands import _mobile_status
        status_obj = SimpleNamespace(
            running=False,
            pid=None,
            bind_host=None,
            bind_port=None,
            paired_devices=0,
            active_elevations=0,
            uptime_seconds=None,
            started_at=None,
        )
        mock_server = MagicMock()
        mock_server.return_value.get_status.return_value = status_obj
        mock_module = MagicMock()
        mock_module.GatewayServer = mock_server
        with patch.dict("sys.modules", {"elle.mobile.server": mock_module}):
            output, success = _mobile_status(_make_session())
            assert success is True
            assert "Not running" in output

    def test_status_exception(self):
        from elle.cli.mobile_commands import _mobile_status
        mock_server = MagicMock()
        mock_server.return_value.get_status.side_effect = RuntimeError("oops")
        mock_module = MagicMock()
        mock_module.GatewayServer = mock_server
        with patch.dict("sys.modules", {"elle.mobile.server": mock_module}):
            output, success = _mobile_status(_make_session())
            assert success is False
            assert "Error" in output


# ---------------------------------------------------------------------------
# _mobile_revoke
# ---------------------------------------------------------------------------


class TestMobileRevoke:
    def test_revoke_no_device_id(self):
        from elle.cli.mobile_commands import _mobile_revoke
        output, success = _mobile_revoke(None, _make_session())
        assert success is False
        assert "Usage" in output

    def test_revoke_device_not_found(self):
        from elle.cli.mobile_commands import _mobile_revoke
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = []
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_models = MagicMock()
        with patch.dict("sys.modules", {
            "elle.mobile.store": mock_store_mod,
            "elle.mobile.elevation": mock_elev_mod,
            "elle.mobile.models": mock_models,
        }):
            output, success = _mobile_revoke("abc123", _make_session())
            assert success is False
            assert "not found" in output

    def test_revoke_multiple_matches(self):
        from elle.cli.mobile_commands import _mobile_revoke
        d1 = SimpleNamespace(device_id="abc123xxx", name="Phone 1")
        d2 = SimpleNamespace(device_id="abc123yyy", name="Phone 2")
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = [d1, d2]
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_models = MagicMock()
        with patch.dict("sys.modules", {
            "elle.mobile.store": mock_store_mod,
            "elle.mobile.elevation": mock_elev_mod,
            "elle.mobile.models": mock_models,
        }):
            output, success = _mobile_revoke("abc123", _make_session())
            assert success is False
            assert "Multiple matches" in output


# ---------------------------------------------------------------------------
# _mobile_approve
# ---------------------------------------------------------------------------


class TestMobileApprove:
    def test_approve_no_args(self):
        from elle.cli.mobile_commands import _mobile_approve
        output, success = _mobile_approve([], _make_session())
        assert success is False
        assert "Usage" in output

    def test_approve_device_not_found(self):
        from elle.cli.mobile_commands import _mobile_approve
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = []
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_models = MagicMock()
        with patch.dict("sys.modules", {
            "elle.mobile.store": mock_store_mod,
            "elle.mobile.elevation": mock_elev_mod,
            "elle.mobile.models": mock_models,
        }):
            output, success = _mobile_approve(["abc123"], _make_session())
            assert success is False
            assert "not found" in output


# ---------------------------------------------------------------------------
# _mobile_audit
# ---------------------------------------------------------------------------


class TestMobileAudit:
    def test_audit_no_entries(self):
        from elle.cli.mobile_commands import _mobile_audit
        mock_audit = MagicMock()
        mock_audit.return_value.get_recent.return_value = []
        mock_mod = MagicMock()
        mock_mod.MobileAuditStore = mock_audit
        with patch.dict("sys.modules", {"elle.mobile.audit": mock_mod}):
            output, success = _mobile_audit([], _make_session())
            assert success is True
            assert "No audit entries" in output

    def test_audit_with_entries(self):
        from elle.cli.mobile_commands import _mobile_audit
        entry = SimpleNamespace(
            timestamp=datetime(2024, 3, 15, 10, 30, 0),
            action=SimpleNamespace(value="pair"),
            success=True,
            device_name="MyPhone",
            device_id="abc12345",
            endpoint=None,
            error=None,
        )
        mock_audit = MagicMock()
        mock_audit.return_value.get_recent.return_value = [entry]
        mock_mod = MagicMock()
        mock_mod.MobileAuditStore = mock_audit
        with patch.dict("sys.modules", {"elle.mobile.audit": mock_mod}):
            output, success = _mobile_audit([], _make_session())
            assert success is True
            assert "Audit Log" in output
            assert "MyPhone" in output

    def test_audit_exception(self):
        from elle.cli.mobile_commands import _mobile_audit
        mock_audit = MagicMock()
        mock_audit.return_value.get_recent.side_effect = RuntimeError("db error")
        mock_mod = MagicMock()
        mock_mod.MobileAuditStore = mock_audit
        with patch.dict("sys.modules", {"elle.mobile.audit": mock_mod}):
            output, success = _mobile_audit([], _make_session())
            assert success is False
            assert "Error" in output

    def test_audit_with_hours_arg(self):
        from elle.cli.mobile_commands import _mobile_audit
        mock_audit = MagicMock()
        mock_audit.return_value.get_recent.return_value = []
        mock_mod = MagicMock()
        mock_mod.MobileAuditStore = mock_audit
        with patch.dict("sys.modules", {"elle.mobile.audit": mock_mod}):
            output, success = _mobile_audit(["--hours", "48"], _make_session())
            assert success is True
            mock_audit.return_value.get_recent.assert_called_once_with(hours=48, limit=50)

    def test_audit_entry_with_endpoint_and_error(self):
        from elle.cli.mobile_commands import _mobile_audit
        entry = SimpleNamespace(
            timestamp=datetime(2024, 3, 15, 10, 30, 0),
            action=SimpleNamespace(value="revoke"),
            success=False,
            device_name=None,
            device_id="abcdef12",
            endpoint="/api/v1/status",
            error="Connection refused",
        )
        mock_audit = MagicMock()
        mock_audit.return_value.get_recent.return_value = [entry]
        mock_mod = MagicMock()
        mock_mod.MobileAuditStore = mock_audit
        with patch.dict("sys.modules", {"elle.mobile.audit": mock_mod}):
            output, success = _mobile_audit([], _make_session())
            assert success is True
            assert "FAIL" in output
            assert "Endpoint" in output
            assert "Error" in output


# ---------------------------------------------------------------------------
# _mobile_devices
# ---------------------------------------------------------------------------


class TestMobileDevices:
    def test_no_devices(self):
        from elle.cli.mobile_commands import _mobile_devices
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = []
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_models = MagicMock()
        with patch.dict("sys.modules", {
            "elle.mobile.store": mock_store_mod,
            "elle.mobile.elevation": mock_elev_mod,
            "elle.mobile.models": mock_models,
        }):
            output, success = _mobile_devices(_make_session())
            assert success is True
            assert "No paired devices" in output

    def test_exception(self):
        from elle.cli.mobile_commands import _mobile_devices
        mock_store = MagicMock()
        mock_store.return_value.list_devices.side_effect = RuntimeError("db error")
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_models = MagicMock()
        with patch.dict("sys.modules", {
            "elle.mobile.store": mock_store_mod,
            "elle.mobile.elevation": mock_elev_mod,
            "elle.mobile.models": mock_models,
        }):
            output, success = _mobile_devices(_make_session())
            assert success is False
            assert "Error" in output


# ---------------------------------------------------------------------------
# _mobile_up
# ---------------------------------------------------------------------------


class TestMobileUp:
    def test_up_server_error(self):
        from elle.cli.mobile_commands import _mobile_up
        # Build mock modules that raise ServerError on start
        mock_config_mod = MagicMock()
        mock_models = MagicMock()
        mock_pairing = MagicMock()
        mock_server_mod = MagicMock()
        # Create a real exception class for ServerError
        server_error_cls = type("ServerError", (Exception,), {})
        mock_server_mod.ServerError = server_error_cls
        mock_server_mod.GatewayServer.return_value.start.side_effect = server_error_cls("port in use")
        with patch.dict("sys.modules", {
            "elle.mobile.config": mock_config_mod,
            "elle.mobile.models": mock_models,
            "elle.mobile.pairing": mock_pairing,
            "elle.mobile.server": mock_server_mod,
        }):
            output, success = _mobile_up([], _make_session())
            assert success is False
            assert "Failed to start" in output

    def test_up_generic_exception(self):
        from elle.cli.mobile_commands import _mobile_up
        mock_config_mod = MagicMock()
        mock_config_mod.get_mobile_config.side_effect = RuntimeError("kaboom")
        mock_models = MagicMock()
        mock_pairing = MagicMock()
        mock_server_mod = MagicMock()
        mock_server_mod.ServerError = type("ServerError", (Exception,), {})
        with patch.dict("sys.modules", {
            "elle.mobile.config": mock_config_mod,
            "elle.mobile.models": mock_models,
            "elle.mobile.pairing": mock_pairing,
            "elle.mobile.server": mock_server_mod,
        }):
            output, success = _mobile_up([], _make_session())
            assert success is False
            assert "Error" in output

    def test_up_invalid_port(self):
        from elle.cli.mobile_commands import _mobile_up
        mock_config_mod = MagicMock()
        mock_models = MagicMock()
        mock_pairing = MagicMock()
        mock_server_mod = MagicMock()
        mock_server_mod.ServerError = type("ServerError", (Exception,), {})
        with patch.dict("sys.modules", {
            "elle.mobile.config": mock_config_mod,
            "elle.mobile.models": mock_models,
            "elle.mobile.pairing": mock_pairing,
            "elle.mobile.server": mock_server_mod,
        }):
            output, success = _mobile_up(["--port", "notanumber"], _make_session())
            assert success is False
            assert "Invalid port" in output
