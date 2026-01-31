"""Tests for mobile_commands.py - mobile gateway REPL commands."""

from __future__ import annotations

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

        assert Colors.BOLD == "\033[1m"


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
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
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
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
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
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
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
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
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
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
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
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.config": mock_config_mod,
                "elle.mobile.models": mock_models,
                "elle.mobile.pairing": mock_pairing,
                "elle.mobile.server": mock_server_mod,
            },
        ):
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
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.config": mock_config_mod,
                "elle.mobile.models": mock_models,
                "elle.mobile.pairing": mock_pairing,
                "elle.mobile.server": mock_server_mod,
            },
        ):
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
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.config": mock_config_mod,
                "elle.mobile.models": mock_models,
                "elle.mobile.pairing": mock_pairing,
                "elle.mobile.server": mock_server_mod,
            },
        ):
            output, success = _mobile_up(["--port", "notanumber"], _make_session())
            assert success is False
            assert "Invalid port" in output

    def test_up_success_with_qr(self):
        """Test successful gateway start with QR code available."""
        from elle.cli.mobile_commands import _mobile_up

        mock_config_mod = MagicMock()
        mock_config_mod.get_mobile_config.return_value = SimpleNamespace(
            enabled=True,
            bind_host="0.0.0.0",
            bind_port=8379,
            overlay_host=None,
            pairing_token_ttl_seconds=300,
            max_paired_devices=5,
            default_elevation_ttl_seconds=600,
            max_elevation_ttl_seconds=3600,
            default_role="readonly",
            internal_api_host="127.0.0.1",
            internal_api_port=8380,
            cert_dir="/tmp/certs",
            db_path="/tmp/mobile.db",
            audit_db_path="/tmp/audit.db",
        )
        mock_models = MagicMock()
        mock_pairing = MagicMock()
        mock_pairing.QRCODE_AVAILABLE = True
        payload = SimpleNamespace(server_fingerprint="abcdef1234567890abcd")
        mock_pairing.PairingManager.return_value.initiate_pairing.return_value = (payload, "token123")
        mock_pairing.PairingManager.return_value.generate_qr_terminal.return_value = "[QR]"
        mock_server_mod = MagicMock()
        mock_server_mod.ServerError = type("ServerError", (Exception,), {})
        status_obj = SimpleNamespace(bind_host="0.0.0.0", bind_port=8379, pid=1234)
        mock_server_mod.GatewayServer.return_value.start.return_value = status_obj

        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.config": mock_config_mod,
                "elle.mobile.models": mock_models,
                "elle.mobile.pairing": mock_pairing,
                "elle.mobile.server": mock_server_mod,
            },
        ):
            output, success = _mobile_up([], _make_session())
            assert success is True
            assert "Started" in output
            assert "[QR]" in output

    def test_up_success_no_qr(self):
        """Test successful gateway start without QR code available."""
        from elle.cli.mobile_commands import _mobile_up

        mock_config_mod = MagicMock()
        mock_config_mod.get_mobile_config.return_value = SimpleNamespace(
            enabled=True,
            bind_host="0.0.0.0",
            bind_port=8379,
            overlay_host=None,
            pairing_token_ttl_seconds=300,
            max_paired_devices=5,
            default_elevation_ttl_seconds=600,
            max_elevation_ttl_seconds=3600,
            default_role="readonly",
            internal_api_host="127.0.0.1",
            internal_api_port=8380,
            cert_dir="/tmp/certs",
            db_path="/tmp/mobile.db",
            audit_db_path="/tmp/audit.db",
        )
        mock_models = MagicMock()
        mock_pairing = MagicMock()
        mock_pairing.QRCODE_AVAILABLE = False
        mock_server_mod = MagicMock()
        mock_server_mod.ServerError = type("ServerError", (Exception,), {})
        status_obj = SimpleNamespace(bind_host="0.0.0.0", bind_port=8379, pid=1234)
        mock_server_mod.GatewayServer.return_value.start.return_value = status_obj

        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.config": mock_config_mod,
                "elle.mobile.models": mock_models,
                "elle.mobile.pairing": mock_pairing,
                "elle.mobile.server": mock_server_mod,
            },
        ):
            output, success = _mobile_up([], _make_session())
            assert success is True
            assert "Started" in output
            assert "qrcode" in output.lower() or "pip install" in output

    def test_up_with_port_override(self):
        """Test gateway start with custom port."""
        from elle.cli.mobile_commands import _mobile_up

        mock_config_mod = MagicMock()
        base_config = SimpleNamespace(
            enabled=True,
            bind_host="0.0.0.0",
            bind_port=8379,
            overlay_host=None,
            pairing_token_ttl_seconds=300,
            max_paired_devices=5,
            default_elevation_ttl_seconds=600,
            max_elevation_ttl_seconds=3600,
            default_role="readonly",
            internal_api_host="127.0.0.1",
            internal_api_port=8380,
            cert_dir="/tmp/certs",
            db_path="/tmp/mobile.db",
            audit_db_path="/tmp/audit.db",
        )
        mock_config_mod.get_mobile_config.return_value = base_config
        mock_config_mod.MobileGatewayConfig.return_value = base_config
        mock_models = MagicMock()
        mock_pairing = MagicMock()
        mock_pairing.QRCODE_AVAILABLE = False
        mock_server_mod = MagicMock()
        mock_server_mod.ServerError = type("ServerError", (Exception,), {})
        status_obj = SimpleNamespace(bind_host="0.0.0.0", bind_port=9999, pid=5678)
        mock_server_mod.GatewayServer.return_value.start.return_value = status_obj

        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.config": mock_config_mod,
                "elle.mobile.models": mock_models,
                "elle.mobile.pairing": mock_pairing,
                "elle.mobile.server": mock_server_mod,
            },
        ):
            output, success = _mobile_up(["--port", "9999"], _make_session())
            assert success is True

    def test_up_with_operator_flag(self):
        """Test gateway start with --operator flag."""
        from elle.cli.mobile_commands import _mobile_up

        mock_config_mod = MagicMock()
        mock_config_mod.get_mobile_config.return_value = SimpleNamespace(
            enabled=True,
            bind_host="0.0.0.0",
            bind_port=8379,
            overlay_host=None,
            pairing_token_ttl_seconds=300,
            max_paired_devices=5,
            default_elevation_ttl_seconds=600,
            max_elevation_ttl_seconds=3600,
            default_role="readonly",
            internal_api_host="127.0.0.1",
            internal_api_port=8380,
            cert_dir="/tmp/certs",
            db_path="/tmp/mobile.db",
            audit_db_path="/tmp/audit.db",
        )
        mock_models = MagicMock()
        mock_pairing = MagicMock()
        mock_pairing.QRCODE_AVAILABLE = False
        mock_server_mod = MagicMock()
        mock_server_mod.ServerError = type("ServerError", (Exception,), {})
        status_obj = SimpleNamespace(bind_host="0.0.0.0", bind_port=8379, pid=1234)
        mock_server_mod.GatewayServer.return_value.start.return_value = status_obj

        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.config": mock_config_mod,
                "elle.mobile.models": mock_models,
                "elle.mobile.pairing": mock_pairing,
                "elle.mobile.server": mock_server_mod,
            },
        ):
            output, success = _mobile_up(["--operator"], _make_session())
            assert success is True

    def test_up_qr_generation_fails(self):
        """Test successful start but QR generation raises exception."""
        from elle.cli.mobile_commands import _mobile_up

        mock_config_mod = MagicMock()
        mock_config_mod.get_mobile_config.return_value = SimpleNamespace(
            enabled=True,
            bind_host="0.0.0.0",
            bind_port=8379,
            overlay_host=None,
            pairing_token_ttl_seconds=300,
            max_paired_devices=5,
            default_elevation_ttl_seconds=600,
            max_elevation_ttl_seconds=3600,
            default_role="readonly",
            internal_api_host="127.0.0.1",
            internal_api_port=8380,
            cert_dir="/tmp/certs",
            db_path="/tmp/mobile.db",
            audit_db_path="/tmp/audit.db",
        )
        mock_models = MagicMock()
        mock_pairing = MagicMock()
        mock_pairing.QRCODE_AVAILABLE = True
        payload = SimpleNamespace(server_fingerprint="abcdef1234567890abcd")
        mock_pairing.PairingManager.return_value.initiate_pairing.return_value = (payload, "token")
        mock_pairing.PairingManager.return_value.generate_qr_terminal.side_effect = RuntimeError("qr fail")
        mock_server_mod = MagicMock()
        mock_server_mod.ServerError = type("ServerError", (Exception,), {})
        status_obj = SimpleNamespace(bind_host="0.0.0.0", bind_port=8379, pid=1234)
        mock_server_mod.GatewayServer.return_value.start.return_value = status_obj

        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.config": mock_config_mod,
                "elle.mobile.models": mock_models,
                "elle.mobile.pairing": mock_pairing,
                "elle.mobile.server": mock_server_mod,
            },
        ):
            output, success = _mobile_up([], _make_session())
            assert success is True
            assert "QR generation failed" in output


# ---------------------------------------------------------------------------
# Extended: handle_mobile_command routing for aliases
# ---------------------------------------------------------------------------


class TestHandleMobileCommandRoutingExtended:
    def test_start_alias(self):
        """start should be alias for up."""
        from elle.cli.mobile_commands import handle_mobile_command

        with patch("elle.cli.mobile_commands._mobile_up") as mock_up:
            mock_up.return_value = ("started", True)
            output, success = handle_mobile_command("/mobile start", _make_session())
            assert success is True

    def test_stop_alias(self):
        """stop should be alias for down."""
        from elle.cli.mobile_commands import handle_mobile_command

        with patch("elle.cli.mobile_commands._mobile_down") as mock_down:
            mock_down.return_value = ("stopped", True)
            output, success = handle_mobile_command("/mobile stop", _make_session())
            assert success is True

    def test_list_alias(self):
        """list should be alias for devices."""
        from elle.cli.mobile_commands import handle_mobile_command

        with patch("elle.cli.mobile_commands._mobile_devices") as mock_devices:
            mock_devices.return_value = ("devices list", True)
            output, success = handle_mobile_command("/mobile list", _make_session())
            assert success is True

    def test_logs_alias(self):
        """logs should be alias for audit."""
        from elle.cli.mobile_commands import handle_mobile_command

        with patch("elle.cli.mobile_commands._mobile_audit") as mock_audit:
            mock_audit.return_value = ("audit log", True)
            output, success = handle_mobile_command("/mobile logs", _make_session())
            assert success is True

    def test_elevate_alias(self):
        """elevate should be alias for approve."""
        from elle.cli.mobile_commands import handle_mobile_command

        with patch("elle.cli.mobile_commands._mobile_approve") as mock_approve:
            mock_approve.return_value = ("elevated", True)
            output, success = handle_mobile_command("/mobile elevate dev123", _make_session())
            assert success is True


# ---------------------------------------------------------------------------
# Extended: _mobile_status with no uptime
# ---------------------------------------------------------------------------


class TestMobileStatusExtended:
    def test_status_running_no_uptime(self):
        from elle.cli.mobile_commands import _mobile_status

        status_obj = SimpleNamespace(
            running=True,
            pid=999,
            bind_host="0.0.0.0",
            bind_port=8379,
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
            assert "Running" in output
            # Should not crash on None uptime


# ---------------------------------------------------------------------------
# Extended: _mobile_devices with various device states
# ---------------------------------------------------------------------------


class TestMobileDevicesExtended:
    def test_devices_paired_status(self):
        from elle.cli.mobile_commands import _mobile_devices

        DeviceStatus = type("DeviceStatus", (), {"PAIRED": "paired", "REVOKED": "revoked"})
        DeviceStatus.PAIRED = SimpleNamespace(value="paired")
        DeviceStatus.REVOKED = SimpleNamespace(value="revoked")

        device = SimpleNamespace(
            device_id="abc12345678",
            name="MyPhone",
            status=DeviceStatus.PAIRED,
            role=SimpleNamespace(value="readonly"),
            last_seen_at=datetime(2024, 3, 15, 10, 30),
            paired_at=datetime(2024, 3, 10, 8, 0),
        )
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = [device]
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_elev_mod.ElevationManager.return_value.get_elevation_status.return_value = {
            "elevated": False,
        }
        mock_models = MagicMock()
        mock_models.DeviceStatus = DeviceStatus
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
            output, success = _mobile_devices(_make_session())
            assert success is True
            assert "MyPhone" in output
            assert "abc12345" in output

    def test_devices_revoked_status(self):
        from elle.cli.mobile_commands import _mobile_devices

        DeviceStatus = type("DeviceStatus", (), {})
        DeviceStatus.PAIRED = SimpleNamespace(value="paired")
        DeviceStatus.REVOKED = SimpleNamespace(value="revoked")

        device = SimpleNamespace(
            device_id="def99999999",
            name="OldPhone",
            status=DeviceStatus.REVOKED,
            role=SimpleNamespace(value="readonly"),
            last_seen_at=None,
            paired_at=None,
        )
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = [device]
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_elev_mod.ElevationManager.return_value.get_elevation_status.return_value = {
            "elevated": False,
        }
        mock_models = MagicMock()
        mock_models.DeviceStatus = DeviceStatus
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
            output, success = _mobile_devices(_make_session())
            assert success is True
            assert "revoked" in output

    def test_devices_elevated(self):
        from elle.cli.mobile_commands import _mobile_devices

        DeviceStatus = type("DeviceStatus", (), {})
        DeviceStatus.PAIRED = SimpleNamespace(value="paired")
        DeviceStatus.REVOKED = SimpleNamespace(value="revoked")

        device = SimpleNamespace(
            device_id="abc12345678",
            name="ElevatedPhone",
            status=DeviceStatus.PAIRED,
            role=SimpleNamespace(value="readonly"),
            last_seen_at=None,
            paired_at=None,
        )
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = [device]
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_elev_mod.ElevationManager.return_value.get_elevation_status.return_value = {
            "elevated": True,
            "effective_role": "operator",
        }
        mock_models = MagicMock()
        mock_models.DeviceStatus = DeviceStatus
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
            output, success = _mobile_devices(_make_session())
            assert success is True
            assert "elevated" in output
            assert "operator" in output


# ---------------------------------------------------------------------------
# Extended: _mobile_revoke full flow
# ---------------------------------------------------------------------------


class TestMobileRevokeExtended:
    def test_revoke_already_revoked(self):
        from elle.cli.mobile_commands import _mobile_revoke

        DeviceStatus = type("DeviceStatus", (), {})
        DeviceStatus.PAIRED = SimpleNamespace(value="paired")
        DeviceStatus.REVOKED = SimpleNamespace(value="revoked")

        device = SimpleNamespace(
            device_id="abc12345678",
            name="Phone",
            status=DeviceStatus.REVOKED,
        )
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = [device]
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_models = MagicMock()
        mock_models.DeviceStatus = DeviceStatus
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
            output, success = _mobile_revoke("abc12345678", _make_session())
            assert success is True
            assert "already revoked" in output

    def test_revoke_confirmed(self):
        from elle.cli.mobile_commands import _mobile_revoke

        DeviceStatus = type("DeviceStatus", (), {})
        DeviceStatus.PAIRED = SimpleNamespace(value="paired")
        DeviceStatus.REVOKED = SimpleNamespace(value="revoked")

        device = SimpleNamespace(
            device_id="abc12345678",
            name="Phone",
            status=DeviceStatus.PAIRED,
        )
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = [device]
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_models = MagicMock()
        mock_models.DeviceStatus = DeviceStatus
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
            with patch("builtins.input", return_value="y"):
                with patch.dict("sys.modules", {"elle.cli.agentic.incident_recorder": MagicMock()}):
                    output, success = _mobile_revoke("abc12345678", _make_session())
                    assert success is True
                    assert "Revoked" in output

    def test_revoke_cancelled(self):
        from elle.cli.mobile_commands import _mobile_revoke

        DeviceStatus = type("DeviceStatus", (), {})
        DeviceStatus.PAIRED = SimpleNamespace(value="paired")
        DeviceStatus.REVOKED = SimpleNamespace(value="revoked")

        device = SimpleNamespace(
            device_id="abc12345678",
            name="Phone",
            status=DeviceStatus.PAIRED,
        )
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = [device]
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_models = MagicMock()
        mock_models.DeviceStatus = DeviceStatus
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
            with patch("builtins.input", return_value="n"):
                output, success = _mobile_revoke("abc12345678", _make_session())
                assert success is False
                assert "Cancelled" in output

    def test_revoke_eof_during_input(self):
        from elle.cli.mobile_commands import _mobile_revoke

        DeviceStatus = type("DeviceStatus", (), {})
        DeviceStatus.PAIRED = SimpleNamespace(value="paired")
        DeviceStatus.REVOKED = SimpleNamespace(value="revoked")

        device = SimpleNamespace(
            device_id="abc12345678",
            name="Phone",
            status=DeviceStatus.PAIRED,
        )
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = [device]
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_models = MagicMock()
        mock_models.DeviceStatus = DeviceStatus
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
            with patch("builtins.input", side_effect=EOFError):
                output, success = _mobile_revoke("abc12345678", _make_session())
                assert success is False
                assert "Cancelled" in output

    def test_revoke_exception(self):
        from elle.cli.mobile_commands import _mobile_revoke

        mock_store = MagicMock()
        mock_store.return_value.list_devices.side_effect = RuntimeError("db error")
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_models = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
            output, success = _mobile_revoke("abc123", _make_session())
            assert success is False
            assert "Error" in output


# ---------------------------------------------------------------------------
# Extended: _mobile_approve full flow
# ---------------------------------------------------------------------------


class TestMobileApproveExtended:
    def test_approve_multiple_matches(self):
        from elle.cli.mobile_commands import _mobile_approve

        d1 = SimpleNamespace(device_id="abc123xxx", name="Phone 1")
        d2 = SimpleNamespace(device_id="abc123yyy", name="Phone 2")
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = [d1, d2]
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_models = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
            output, success = _mobile_approve(["abc123"], _make_session())
            assert success is False
            assert "Multiple matches" in output

    def test_approve_device_not_paired(self):
        from elle.cli.mobile_commands import _mobile_approve

        DeviceStatus = type("DeviceStatus", (), {})
        DeviceStatus.PAIRED = SimpleNamespace(value="paired")
        DeviceStatus.REVOKED = SimpleNamespace(value="revoked")

        device = SimpleNamespace(
            device_id="abc12345678",
            name="Phone",
            status=DeviceStatus.REVOKED,
        )
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = [device]
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_models = MagicMock()
        mock_models.DeviceStatus = DeviceStatus
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
            output, success = _mobile_approve(["abc12345678"], _make_session())
            assert success is False
            assert "not paired" in output.lower() or "not paired" in output

    def test_approve_invalid_ttl(self):
        from elle.cli.mobile_commands import _mobile_approve

        DeviceStatus = type("DeviceStatus", (), {})
        DeviceStatus.PAIRED = SimpleNamespace(value="paired")

        device = SimpleNamespace(
            device_id="abc12345678",
            name="Phone",
            status=DeviceStatus.PAIRED,
        )
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = [device]
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_elev_mod.parse_ttl.side_effect = ValueError("bad ttl")
        mock_models = MagicMock()
        mock_models.DeviceStatus = DeviceStatus
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
            output, success = _mobile_approve(["abc12345678", "--ttl", "bad"], _make_session())
            assert success is False
            assert "Invalid TTL" in output

    def test_approve_success(self):
        from elle.cli.mobile_commands import _mobile_approve

        DeviceStatus = type("DeviceStatus", (), {})
        DeviceStatus.PAIRED = SimpleNamespace(value="paired")

        device = SimpleNamespace(
            device_id="abc12345678",
            name="Phone",
            status=DeviceStatus.PAIRED,
        )
        mock_store = MagicMock()
        mock_store.return_value.list_devices.return_value = [device]
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_elev_mod.parse_ttl.return_value = 600
        mock_elev_mod.format_ttl.return_value = "10m"
        mock_elev_mod.ElevationError = type("ElevationError", (Exception,), {})
        elevation_result = SimpleNamespace(
            elevated_role=SimpleNamespace(value="operator"),
            expires_at=datetime(2024, 12, 31, 23, 59, 59),
        )
        mock_elev_mod.ElevationManager.return_value.grant_elevation.return_value = elevation_result
        mock_models = MagicMock()
        mock_models.DeviceStatus = DeviceStatus
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
                "elle.cli.agentic.incident_recorder": MagicMock(),
            },
        ):
            output, success = _mobile_approve(["abc12345678"], _make_session())
            assert success is True
            assert "Elevated" in output

    def test_approve_general_exception(self):
        from elle.cli.mobile_commands import _mobile_approve

        mock_store = MagicMock()
        mock_store.return_value.list_devices.side_effect = RuntimeError("db error")
        mock_store_mod = MagicMock()
        mock_store_mod.MobileStore = mock_store
        mock_elev_mod = MagicMock()
        mock_elev_mod.ElevationError = type("ElevationError", (Exception,), {})
        mock_models = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "elle.mobile.store": mock_store_mod,
                "elle.mobile.elevation": mock_elev_mod,
                "elle.mobile.models": mock_models,
            },
        ):
            output, success = _mobile_approve(["abc123"], _make_session())
            assert success is False
            assert "Error" in output


# ---------------------------------------------------------------------------
# Extended: _mobile_audit argument parsing
# ---------------------------------------------------------------------------


class TestMobileAuditExtended:
    def test_audit_with_limit_arg(self):
        from elle.cli.mobile_commands import _mobile_audit

        mock_audit = MagicMock()
        mock_audit.return_value.get_recent.return_value = []
        mock_mod = MagicMock()
        mock_mod.MobileAuditStore = mock_audit
        with patch.dict("sys.modules", {"elle.mobile.audit": mock_mod}):
            output, success = _mobile_audit(["--limit", "10"], _make_session())
            assert success is True
            mock_audit.return_value.get_recent.assert_called_once_with(hours=24, limit=10)

    def test_audit_with_unknown_arg(self):
        from elle.cli.mobile_commands import _mobile_audit

        mock_audit = MagicMock()
        mock_audit.return_value.get_recent.return_value = []
        mock_mod = MagicMock()
        mock_mod.MobileAuditStore = mock_audit
        with patch.dict("sys.modules", {"elle.mobile.audit": mock_mod}):
            output, success = _mobile_audit(["--unknown", "val"], _make_session())
            assert success is True

    def test_audit_action_gateway_start(self):
        from elle.cli.mobile_commands import _mobile_audit

        entry = SimpleNamespace(
            timestamp=datetime(2024, 3, 15, 10, 30, 0),
            action=SimpleNamespace(value="gateway_start"),
            success=True,
            device_name=None,
            device_id=None,
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
            assert "gateway_start" in output

    def test_audit_action_elevate(self):
        from elle.cli.mobile_commands import _mobile_audit

        entry = SimpleNamespace(
            timestamp=datetime(2024, 3, 15, 10, 30, 0),
            action=SimpleNamespace(value="elevate"),
            success=True,
            device_name=None,
            device_id=None,
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
            assert "elevate" in output

    def test_audit_action_other(self):
        from elle.cli.mobile_commands import _mobile_audit

        entry = SimpleNamespace(
            timestamp=datetime(2024, 3, 15, 10, 30, 0),
            action=SimpleNamespace(value="other_action"),
            success=True,
            device_name=None,
            device_id=None,
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
            assert "other_action" in output
