from __future__ import annotations

"""Tests for the ELLE Terminal REPL (src/elle/cli/terminal/repl.py)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# =============================================================================
# Mock all elle-internal imports BEFORE importing the module under test.
# We patch at the module-level names used by repl.py.
# =============================================================================

MODULE = "elle.cli.terminal.repl"


@pytest.fixture(autouse=True)
def _patch_ui():
    """Patch every UI symbol, setup helpers, and session factory that repl.py imports."""
    with (
        patch(f"{MODULE}.console") as mock_console,
        patch(f"{MODULE}.clear") as mock_clear,
        patch(f"{MODULE}.print_banner") as mock_print_banner,
        patch(f"{MODULE}.print_error") as mock_print_error,
        patch(f"{MODULE}.print_muted") as mock_print_muted,
        patch(f"{MODULE}.render_welcome_back") as mock_render_wb,
        patch(f"{MODULE}.EllePrompt") as mock_prompt_cls,
        patch(f"{MODULE}.is_first_run", return_value=False) as mock_is_first_run,
        patch(f"{MODULE}.run_setup_wizard", return_value=True) as mock_run_setup_wizard,
    ):
        yield {
            "console": mock_console,
            "clear": mock_clear,
            "print_banner": mock_print_banner,
            "print_error": mock_print_error,
            "print_muted": mock_print_muted,
            "render_welcome_back": mock_render_wb,
            "EllePrompt": mock_prompt_cls,
            "is_first_run": mock_is_first_run,
            "run_setup_wizard": mock_run_setup_wizard,
        }


@pytest.fixture()
def ui_mocks(_patch_ui):
    """Expose the patched UI dict for tests that need explicit access."""
    return _patch_ui


@pytest.fixture()
def mock_engine():
    """Return a MagicMock that behaves like Engine."""
    engine = MagicMock()
    return engine


@pytest.fixture()
def mock_session():
    """Return a MagicMock that behaves like Session."""
    return MagicMock()


@pytest.fixture()
def make_repl(mock_engine, mock_session, _patch_ui):
    """Factory that builds a REPL with mocked deps, returning (repl, ui_dict).

    The startup helper methods (_show_banner, _show_startup_notices,
    _maybe_show_sponsor_tip) are stubbed so that tests exercising the
    main loop do not accidentally hit real status-gathering code.
    """
    from elle.cli.terminal.repl import REPL

    repl = REPL(engine=mock_engine, session=mock_session)
    # The EllePrompt constructor was called inside __init__; replace the
    # resulting prompt attribute with a fresh MagicMock so tests can
    # configure prompt.prompt() return values easily.
    repl.prompt = MagicMock()
    # Stub startup helpers so run() proceeds straight to the loop.
    repl._show_banner = MagicMock()
    repl._show_startup_notices = MagicMock()
    repl._maybe_show_sponsor_tip = MagicMock()
    return repl, _patch_ui


# =============================================================================
# REPL.__init__ tests
# =============================================================================


class TestREPLInit:
    """Tests for REPL constructor."""

    def test_init_with_explicit_engine_and_session(self, mock_engine, mock_session, ui_mocks):
        from elle.cli.terminal.repl import REPL

        repl = REPL(engine=mock_engine, session=mock_session)
        assert repl.engine is mock_engine
        assert repl.session is mock_session
        assert repl._running is False

    def test_init_defaults_to_get_engine_and_create_session(self, ui_mocks):
        with (
            patch(f"{MODULE}.get_engine") as mock_get_engine,
            patch(f"{MODULE}.create_session") as mock_create_session,
        ):
            mock_get_engine.return_value = MagicMock()
            mock_create_session.return_value = MagicMock()

            from elle.cli.terminal.repl import REPL

            repl = REPL()
            mock_get_engine.assert_called_once()
            mock_create_session.assert_called_once()
            assert repl.engine is mock_get_engine.return_value
            assert repl.session is mock_create_session.return_value

    def test_init_creates_elle_prompt_with_history(self, mock_engine, mock_session, ui_mocks):
        from elle.cli.terminal.repl import HISTORY_FILE

        ui_mocks["EllePrompt"].reset_mock()

        from elle.cli.terminal.repl import REPL

        REPL(engine=mock_engine, session=mock_session)
        ui_mocks["EllePrompt"].assert_called_once_with(
            history_file=HISTORY_FILE,
            show_status_bar=True,
        )


# =============================================================================
# HISTORY_FILE constant
# =============================================================================


class TestHistoryFile:
    def test_history_file_in_home(self, ui_mocks):
        from elle.cli.terminal.repl import HISTORY_FILE

        assert Path.home() / ".elle_history" == HISTORY_FILE


# =============================================================================
# REPL.run - normal flow
# =============================================================================


class TestREPLRunNormalFlow:
    """Tests for the main run() loop and its branches."""

    def test_empty_input_continues(self, make_repl, mock_engine):
        """Blank lines should be skipped."""
        repl, ui = make_repl

        # First prompt returns blank, second triggers exit
        repl.prompt.prompt.side_effect = ["", "   ", EOFError]
        result = repl.run()

        # Engine.process should never have been called
        mock_engine.process.assert_not_called()
        assert result == 0

    def test_successful_engine_result_sets_success_state(self, make_repl, mock_engine, mock_session):
        """When engine returns success=True, prompt state is 'success'."""
        from elle.cli.engine import EngineAction

        repl, ui = make_repl

        engine_result = MagicMock()
        engine_result.output = "some output"
        engine_result.success = True
        engine_result.action = EngineAction.CONTINUE
        engine_result.session = mock_session
        mock_engine.process.return_value = engine_result

        repl.prompt.prompt.side_effect = ["hello", EOFError]
        repl.run()

        repl.prompt.set_state.assert_any_call("success")

    def test_failed_engine_result_sets_error_state(self, make_repl, mock_engine, mock_session):
        from elle.cli.engine import EngineAction

        repl, ui = make_repl

        engine_result = MagicMock()
        engine_result.output = "error happened"
        engine_result.success = False
        engine_result.action = EngineAction.CONTINUE
        engine_result.session = mock_session
        mock_engine.process.return_value = engine_result

        repl.prompt.prompt.side_effect = ["bad command", EOFError]
        repl.run()

        repl.prompt.set_state.assert_any_call("error")

    def test_exit_action_stops_loop_success_true(self, make_repl, mock_engine, mock_session):
        """EXIT with success=True: state is 'success' (if-branch), loop ends."""
        from elle.cli.engine import EngineAction

        repl, ui = make_repl

        engine_result = MagicMock()
        engine_result.output = ""
        engine_result.success = True
        engine_result.action = EngineAction.EXIT
        engine_result.session = mock_session
        mock_engine.process.return_value = engine_result

        repl.prompt.prompt.side_effect = ["exit"]
        result = repl.run()

        assert result == 0
        assert repl._running is False
        # success=True takes the if-branch, so state is "success"
        repl.prompt.set_state.assert_any_call("success")

    def test_exit_action_stops_loop_success_false(self, make_repl, mock_engine, mock_session):
        """EXIT with success=False: state is 'idle' (elif-branch), loop ends."""
        from elle.cli.engine import EngineAction

        repl, ui = make_repl

        engine_result = MagicMock()
        engine_result.output = ""
        engine_result.success = False
        engine_result.action = EngineAction.EXIT
        engine_result.session = mock_session
        mock_engine.process.return_value = engine_result

        repl.prompt.prompt.side_effect = ["exit"]
        result = repl.run()

        assert result == 0
        assert repl._running is False
        repl.prompt.set_state.assert_any_call("idle")

    def test_clear_action_calls_clear(self, make_repl, mock_engine, mock_session):
        from elle.cli.engine import EngineAction

        repl, ui = make_repl

        engine_result = MagicMock()
        engine_result.output = ""
        engine_result.success = True
        engine_result.action = EngineAction.CLEAR
        engine_result.session = mock_session
        mock_engine.process.return_value = engine_result

        repl.prompt.prompt.side_effect = ["clear", EOFError]
        repl.run()

        ui["clear"].assert_called_once()

    def test_output_with_rich_renderable(self, make_repl, mock_engine, mock_session):
        """Output objects with __rich__ attribute should be printed via console."""
        from elle.cli.engine import EngineAction

        repl, ui = make_repl

        rich_obj = MagicMock()
        rich_obj.__rich__ = MagicMock()

        engine_result = MagicMock()
        engine_result.output = rich_obj
        engine_result.success = True
        engine_result.action = EngineAction.CONTINUE
        engine_result.session = mock_session
        mock_engine.process.return_value = engine_result

        repl.prompt.prompt.side_effect = ["test", EOFError]
        repl.run()

        ui["console"].print.assert_any_call(rich_obj)

    def test_output_with_rich_console_renderable(self, make_repl, mock_engine, mock_session):
        """Output objects with __rich_console__ attribute should also be printed."""
        from elle.cli.engine import EngineAction

        repl, ui = make_repl

        rich_obj = MagicMock(spec=[])
        rich_obj.__rich_console__ = MagicMock()

        engine_result = MagicMock()
        engine_result.output = rich_obj
        engine_result.success = True
        engine_result.action = EngineAction.CONTINUE
        engine_result.session = mock_session
        mock_engine.process.return_value = engine_result

        repl.prompt.prompt.side_effect = ["test", EOFError]
        repl.run()

        ui["console"].print.assert_any_call(rich_obj)

    def test_plain_text_output(self, make_repl, mock_engine, mock_session):
        """Plain string output without rich attributes is still printed."""
        from elle.cli.engine import EngineAction

        repl, ui = make_repl

        engine_result = MagicMock()
        engine_result.output = "plain text"
        engine_result.success = True
        engine_result.action = EngineAction.CONTINUE
        engine_result.session = mock_session
        mock_engine.process.return_value = engine_result

        repl.prompt.prompt.side_effect = ["test", EOFError]
        repl.run()

        ui["console"].print.assert_any_call("plain text")

    def test_no_output_does_not_print(self, make_repl, mock_engine, mock_session):
        """When result.output is falsy, console.print should not be called for output."""
        from elle.cli.engine import EngineAction

        repl, ui = make_repl

        engine_result = MagicMock()
        engine_result.output = ""
        engine_result.success = True
        engine_result.action = EngineAction.CONTINUE
        engine_result.session = mock_session
        mock_engine.process.return_value = engine_result

        # Reset console.print count before the loop body
        ui["console"].print.reset_mock()

        repl.prompt.prompt.side_effect = ["test", EOFError]
        repl.run()

        # The only console.print calls should be from EOFError handler
        # (which prints "\n[muted]Goodbye.[/muted]") - NOT from output rendering
        for c in ui["console"].print.call_args_list:
            if c.args:
                assert c.args[0] != ""

    def test_session_is_updated_after_engine_process(self, make_repl, mock_engine, mock_session):
        from elle.cli.engine import EngineAction

        repl, ui = make_repl

        new_session = MagicMock()
        engine_result = MagicMock()
        engine_result.output = ""
        engine_result.success = True
        engine_result.action = EngineAction.CONTINUE
        engine_result.session = new_session
        mock_engine.process.return_value = engine_result

        repl.prompt.prompt.side_effect = ["test", EOFError]
        repl.run()

        assert repl.session is new_session


# =============================================================================
# REPL.run - slash command routing
# =============================================================================


class TestREPLSlashCommands:
    """Tests for /reconfigure, /policies, /setup routing."""

    @pytest.mark.parametrize("cmd", ["/reconfigure", "/policies", "/setup"])
    def test_reconfigure_commands_call_handle_reconfigure(self, make_repl, mock_engine, cmd):
        repl, ui = make_repl

        # Reset the autouse-fixture's run_setup_wizard mock so we get a clean count
        ui["run_setup_wizard"].reset_mock()

        repl.prompt.prompt.side_effect = [cmd, EOFError]
        repl.run()

        ui["run_setup_wizard"].assert_called_once_with(repl.prompt, reconfigure=True)
        repl.prompt.set_state.assert_any_call("success")
        mock_engine.process.assert_not_called()

    def test_reconfigure_case_insensitive(self, make_repl, mock_engine):
        repl, ui = make_repl
        ui["run_setup_wizard"].reset_mock()

        repl.prompt.prompt.side_effect = ["/RECONFIGURE", EOFError]
        repl.run()

        ui["run_setup_wizard"].assert_called_once()

    def test_reconfigure_with_leading_trailing_spaces(self, make_repl, mock_engine):
        repl, ui = make_repl
        ui["run_setup_wizard"].reset_mock()

        repl.prompt.prompt.side_effect = ["  /setup  ", EOFError]
        repl.run()

        ui["run_setup_wizard"].assert_called_once()


# =============================================================================
# REPL.run - interrupt handling
# =============================================================================


class TestREPLInterruptHandling:
    def test_keyboard_interrupt_resets_state_and_continues(self, make_repl, mock_engine):
        """Ctrl+C should print newline, set idle, and continue the loop."""
        repl, ui = make_repl

        repl.prompt.prompt.side_effect = [KeyboardInterrupt, EOFError]
        result = repl.run()

        # Should have printed empty line for Ctrl+C
        ui["console"].print.assert_any_call()
        repl.prompt.set_state.assert_any_call("idle")
        assert result == 0

    def test_eof_error_prints_goodbye(self, make_repl):
        """Ctrl+D should print goodbye and exit with 0."""
        repl, ui = make_repl

        repl.prompt.prompt.side_effect = [EOFError]
        result = repl.run()

        ui["console"].print.assert_any_call("\n[muted]Goodbye.[/muted]")
        assert result == 0
        assert repl._running is False

    def test_unexpected_exception_returns_1(self, make_repl):
        """An unexpected exception in the outer try should return exit code 1."""
        repl, ui = make_repl

        # Make the inner while loop raise something unexpected
        repl.prompt.prompt.side_effect = RuntimeError("kaboom")
        result = repl.run()

        ui["print_error"].assert_called_once()
        assert "kaboom" in ui["print_error"].call_args[0][0]
        assert result == 1


# =============================================================================
# REPL._show_banner
# =============================================================================


class TestShowBanner:
    def test_show_banner_collects_status_and_prints(self, mock_engine, mock_session, _patch_ui):
        """_show_banner gathers system status and delegates to prompt + print_banner."""
        from elle.cli.terminal.repl import REPL

        repl = REPL(engine=mock_engine, session=mock_session)
        repl.prompt = MagicMock()

        repl._get_model_name = MagicMock(return_value="qwen2.5:7b")
        repl._get_daemon_status = MagicMock(return_value=("running", 8377))
        repl._get_vault_docs = MagicMock(return_value=42)
        repl._get_open_incidents = MagicMock(return_value=3)

        repl._show_banner()

        repl.prompt.update_status.assert_called_once_with(
            model="qwen2.5:7b",
            daemon_status="running",
            vault_docs=42,
            incidents_open=3,
        )
        _patch_ui["print_banner"].assert_called_once_with(
            model="qwen2.5:7b",
            daemon_status="running",
            api_port=8377,
            vault_docs=42,
            incidents_open=3,
        )

    def test_show_banner_with_none_values(self, mock_engine, mock_session, _patch_ui):
        from elle.cli.terminal.repl import REPL

        repl = REPL(engine=mock_engine, session=mock_session)
        repl.prompt = MagicMock()

        repl._get_model_name = MagicMock(return_value=None)
        repl._get_daemon_status = MagicMock(return_value=("stopped", None))
        repl._get_vault_docs = MagicMock(return_value=None)
        repl._get_open_incidents = MagicMock(return_value=0)

        repl._show_banner()

        _patch_ui["print_banner"].assert_called_once_with(
            model=None,
            daemon_status="stopped",
            api_port=None,
            vault_docs=None,
            incidents_open=0,
        )


# =============================================================================
# REPL._show_startup_notices
# =============================================================================


class TestShowStartupNotices:
    def test_no_notices_when_no_reboot_or_incidents(self, mock_engine, mock_session, _patch_ui):
        from elle.cli.terminal.repl import REPL

        repl = REPL(engine=mock_engine, session=mock_session)
        repl._check_pending_reboot = MagicMock(return_value=False)
        repl._get_new_incidents = MagicMock(return_value=0)

        repl._show_startup_notices()

        _patch_ui["render_welcome_back"].assert_not_called()

    def test_renders_when_pending_reboot(self, mock_engine, mock_session, _patch_ui):
        from elle.cli.terminal.repl import REPL

        repl = REPL(engine=mock_engine, session=mock_session)
        repl._check_pending_reboot = MagicMock(return_value=True)
        repl._get_new_incidents = MagicMock(return_value=0)

        repl._show_startup_notices()

        _patch_ui["render_welcome_back"].assert_called_once_with(
            pending_reboot=True,
            new_incidents=0,
        )

    def test_renders_when_new_incidents(self, mock_engine, mock_session, _patch_ui):
        from elle.cli.terminal.repl import REPL

        repl = REPL(engine=mock_engine, session=mock_session)
        repl._check_pending_reboot = MagicMock(return_value=False)
        repl._get_new_incidents = MagicMock(return_value=5)

        repl._show_startup_notices()

        _patch_ui["render_welcome_back"].assert_called_once_with(
            pending_reboot=False,
            new_incidents=5,
        )

    def test_renders_when_both(self, mock_engine, mock_session, _patch_ui):
        from elle.cli.terminal.repl import REPL

        repl = REPL(engine=mock_engine, session=mock_session)
        repl._check_pending_reboot = MagicMock(return_value=True)
        repl._get_new_incidents = MagicMock(return_value=2)

        repl._show_startup_notices()

        _patch_ui["render_welcome_back"].assert_called_once_with(
            pending_reboot=True,
            new_incidents=2,
        )


# =============================================================================
# REPL._maybe_show_sponsor_tip
# =============================================================================


class TestMaybeShowSponsorTip:
    def _make_path_chain(self, *, flag_exists, mkdir_side_effect=None):
        """Build a mock Path.home() / ".local" / "state" / "elle" / "sponsor_tip_shown" chain."""
        mock_tip_flag = MagicMock()
        mock_tip_flag.exists.return_value = flag_exists

        mock_state_elle = MagicMock()
        mock_state_elle.__truediv__ = MagicMock(return_value=mock_tip_flag)
        if mkdir_side_effect:
            mock_state_elle.mkdir.side_effect = mkdir_side_effect

        mock_state = MagicMock()
        mock_state.__truediv__ = MagicMock(return_value=mock_state_elle)

        mock_local = MagicMock()
        mock_local.__truediv__ = MagicMock(return_value=mock_state)

        mock_home = MagicMock()
        mock_home.__truediv__ = MagicMock(return_value=mock_local)

        return mock_home, mock_state_elle, mock_tip_flag

    def test_shows_tip_when_flag_not_present(self, mock_engine, mock_session, _patch_ui):
        """When the sponsor flag file does not exist, the tip is shown and the flag is created."""
        from elle.cli.terminal.repl import REPL

        repl = REPL(engine=mock_engine, session=mock_session)
        repl.prompt = MagicMock()

        mock_home, mock_state_elle, mock_tip_flag = self._make_path_chain(flag_exists=False)

        with patch(f"{MODULE}.Path") as mock_path_cls:
            mock_path_cls.home.return_value = mock_home
            repl._maybe_show_sponsor_tip()

        mock_state_elle.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_tip_flag.touch.assert_called_once()
        _patch_ui["print_muted"].assert_called_once()
        _patch_ui["console"].print.assert_called()

    def test_does_not_show_tip_when_flag_exists(self, mock_engine, mock_session, _patch_ui):
        """When the sponsor flag file already exists, nothing is printed."""
        from elle.cli.terminal.repl import REPL

        repl = REPL(engine=mock_engine, session=mock_session)
        repl.prompt = MagicMock()

        mock_home, _, _ = self._make_path_chain(flag_exists=True)

        with patch(f"{MODULE}.Path") as mock_path_cls:
            mock_path_cls.home.return_value = mock_home
            repl._maybe_show_sponsor_tip()

        _patch_ui["print_muted"].assert_not_called()

    def test_oserror_on_mkdir_skips_tip(self, mock_engine, mock_session, _patch_ui):
        """When we cannot create the state directory, the tip is silently skipped."""
        from elle.cli.terminal.repl import REPL

        repl = REPL(engine=mock_engine, session=mock_session)
        repl.prompt = MagicMock()

        mock_home, _, _ = self._make_path_chain(flag_exists=False, mkdir_side_effect=OSError("permission denied"))

        with patch(f"{MODULE}.Path") as mock_path_cls:
            mock_path_cls.home.return_value = mock_home
            repl._maybe_show_sponsor_tip()

        _patch_ui["print_muted"].assert_not_called()


# =============================================================================
# REPL._get_model_name
# =============================================================================


class TestGetModelName:
    def test_returns_model_when_available(self, make_repl):
        repl, _ = make_repl

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.model = "qwen2.5:7b-instruct-q8_0"

        with patch.dict(sys.modules, {"elle.rag.llm": MagicMock(get_llm=MagicMock(return_value=mock_llm))}):
            result = repl._get_model_name()

        assert result == "qwen2.5:7b-instruct-q8_0"

    def test_returns_none_when_not_available(self, make_repl):
        repl, _ = make_repl

        mock_llm = MagicMock()
        mock_llm.is_available.return_value = False

        with patch.dict(sys.modules, {"elle.rag.llm": MagicMock(get_llm=MagicMock(return_value=mock_llm))}):
            result = repl._get_model_name()

        assert result is None

    def test_returns_none_on_import_error(self, make_repl):
        repl, _ = make_repl

        # Simulate import failure by making the lazy import raise
        with patch("builtins.__import__", side_effect=ImportError("no module")):
            result = repl._get_model_name()

        assert result is None

    def test_returns_none_on_any_exception(self, make_repl):
        repl, _ = make_repl

        mock_mod = MagicMock()
        mock_mod.get_llm.side_effect = RuntimeError("oops")

        with patch.dict(sys.modules, {"elle.rag.llm": mock_mod}):
            result = repl._get_model_name()

        assert result is None


# =============================================================================
# REPL._get_daemon_status
# =============================================================================


class TestGetDaemonStatus:
    def test_daemon_running(self, make_repl):
        repl, _ = make_repl

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_httpx = MagicMock()
        mock_httpx.get.return_value = mock_response

        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            status, port = repl._get_daemon_status()

        assert status == "running"
        assert port == 8377

    def test_daemon_stopped_non_200(self, make_repl):
        repl, _ = make_repl

        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_httpx = MagicMock()
        mock_httpx.get.return_value = mock_response

        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            status, port = repl._get_daemon_status()

        assert status == "stopped"
        assert port is None

    def test_daemon_starting_with_pid_file(self, make_repl):
        repl, _ = make_repl

        mock_httpx = MagicMock()
        mock_httpx.get.side_effect = ConnectionError("refused")

        mock_pid_path = MagicMock()
        mock_pid_path.exists.return_value = True

        with (
            patch.dict(sys.modules, {"httpx": mock_httpx}),
            patch("elle.cli.terminal.repl.Path") as mock_path_cls,
        ):
            mock_path_cls.home.return_value = Path("/fakehome")
            mock_path_cls.return_value = mock_pid_path
            status, port = repl._get_daemon_status()

        assert status == "starting"
        assert port is None

    def test_daemon_stopped_no_pid_file(self, make_repl):
        repl, _ = make_repl

        mock_httpx = MagicMock()
        mock_httpx.get.side_effect = ConnectionError("refused")

        mock_pid_path = MagicMock()
        mock_pid_path.exists.return_value = False

        with (
            patch.dict(sys.modules, {"httpx": mock_httpx}),
            patch("elle.cli.terminal.repl.Path") as mock_path_cls,
        ):
            mock_path_cls.home.return_value = Path("/fakehome")
            mock_path_cls.return_value = mock_pid_path
            status, port = repl._get_daemon_status()

        assert status == "stopped"
        assert port is None

    def test_daemon_stopped_pid_check_exception(self, make_repl):
        repl, _ = make_repl

        mock_httpx = MagicMock()
        mock_httpx.get.side_effect = ConnectionError("refused")

        with (
            patch.dict(sys.modules, {"httpx": mock_httpx}),
            patch("elle.cli.terminal.repl.Path", side_effect=Exception("bad")),
        ):
            status, port = repl._get_daemon_status()

        assert status == "stopped"
        assert port is None


# =============================================================================
# REPL._get_vault_docs
# =============================================================================


class TestGetVaultDocs:
    def test_returns_doc_count(self, make_repl):
        repl, _ = make_repl

        mock_status = MagicMock()
        mock_status.total_docs = 123

        mock_manvault = MagicMock()
        mock_manvault.get_status.return_value = mock_status

        with patch.dict(sys.modules, {"elle.daemon.manvault": mock_manvault}):
            result = repl._get_vault_docs()

        assert result == 123

    def test_returns_none_when_status_is_none(self, make_repl):
        repl, _ = make_repl

        mock_manvault = MagicMock()
        mock_manvault.get_status.return_value = None

        with patch.dict(sys.modules, {"elle.daemon.manvault": mock_manvault}):
            result = repl._get_vault_docs()

        assert result is None

    def test_returns_none_on_exception(self, make_repl):
        repl, _ = make_repl

        with patch("builtins.__import__", side_effect=ImportError("no module")):
            result = repl._get_vault_docs()

        assert result is None


# =============================================================================
# REPL._get_open_incidents
# =============================================================================


class TestGetOpenIncidents:
    def test_returns_incident_count(self, make_repl):
        repl, _ = make_repl

        mock_store = MagicMock()
        mock_store.get_incident_count.return_value = 7

        with patch.dict(sys.modules, {"elle.daemon.incidents.store": mock_store}):
            result = repl._get_open_incidents()

        assert result == 7

    def test_returns_zero_on_exception(self, make_repl):
        repl, _ = make_repl

        with patch("builtins.__import__", side_effect=ImportError("no module")):
            result = repl._get_open_incidents()

        assert result == 0


# =============================================================================
# REPL._check_pending_reboot
# =============================================================================


class TestCheckPendingReboot:
    def test_returns_true_when_intent_exists(self, make_repl):
        repl, _ = make_repl

        mock_store = MagicMock()
        mock_store.get_active_intent.return_value = MagicMock()

        with patch.dict(sys.modules, {"elle.daemon.reboot.store": mock_store}):
            result = repl._check_pending_reboot()

        assert result is True

    def test_returns_false_when_no_intent(self, make_repl):
        repl, _ = make_repl

        mock_store = MagicMock()
        mock_store.get_active_intent.return_value = None

        with patch.dict(sys.modules, {"elle.daemon.reboot.store": mock_store}):
            result = repl._check_pending_reboot()

        assert result is False

    def test_returns_false_on_exception(self, make_repl):
        repl, _ = make_repl

        with patch("builtins.__import__", side_effect=ImportError("no module")):
            result = repl._check_pending_reboot()

        assert result is False


# =============================================================================
# REPL._get_new_incidents
# =============================================================================


class TestGetNewIncidents:
    def test_returns_zero_always(self, make_repl):
        """Currently a stub that always returns 0."""
        repl, _ = make_repl
        assert repl._get_new_incidents() == 0


# =============================================================================
# REPL._handle_reconfigure
# =============================================================================


class TestHandleReconfigure:
    def test_calls_run_setup_wizard_with_reconfigure_true(self, make_repl):
        repl, ui = make_repl
        ui["run_setup_wizard"].reset_mock()

        repl._handle_reconfigure()

        ui["run_setup_wizard"].assert_called_once_with(repl.prompt, reconfigure=True)


# =============================================================================
# run() entry point
# =============================================================================


class TestRunEntryPoint:
    def test_run_creates_repl_and_exits(self, ui_mocks):
        with (
            patch(f"{MODULE}.REPL") as mock_repl_cls,
            patch(f"{MODULE}.sys") as mock_sys,
        ):
            mock_repl_instance = MagicMock()
            mock_repl_instance.run.return_value = 0
            mock_repl_cls.return_value = mock_repl_instance

            from elle.cli.terminal.repl import run

            run()

            mock_repl_cls.assert_called_once()
            mock_repl_instance.run.assert_called_once()
            mock_sys.exit.assert_called_once_with(0)

    def test_run_exits_with_error_code(self, ui_mocks):
        with (
            patch(f"{MODULE}.REPL") as mock_repl_cls,
            patch(f"{MODULE}.sys") as mock_sys,
        ):
            mock_repl_instance = MagicMock()
            mock_repl_instance.run.return_value = 1
            mock_repl_cls.return_value = mock_repl_instance

            from elle.cli.terminal.repl import run

            run()

            mock_sys.exit.assert_called_once_with(1)


# =============================================================================
# REPL.run - full startup sequence
# =============================================================================


class TestREPLRunStartupSequence:
    """Verify the startup sequence: banner, first-run check, notices, sponsor tip."""

    def test_first_run_wizard_cancel_continues(self, make_repl):
        """When first run wizard is cancelled, continue with default settings."""
        repl, ui = make_repl

        repl._show_banner = MagicMock()
        repl._show_startup_notices = MagicMock()
        repl._maybe_show_sponsor_tip = MagicMock()

        with (
            patch(f"{MODULE}.is_first_run", return_value=True),
            patch(f"{MODULE}.run_setup_wizard", return_value=False),
        ):
            repl.prompt.prompt.side_effect = [EOFError]
            result = repl.run()

        repl._show_banner.assert_called_once()
        ui["print_muted"].assert_any_call("Continuing with default settings...")
        ui["console"].print.assert_called()
        assert result == 0

    def test_first_run_wizard_succeeds(self, make_repl):
        """When first run wizard succeeds, no 'continuing' message."""
        repl, ui = make_repl

        repl._show_banner = MagicMock()
        repl._show_startup_notices = MagicMock()
        repl._maybe_show_sponsor_tip = MagicMock()

        with (
            patch(f"{MODULE}.is_first_run", return_value=True),
            patch(f"{MODULE}.run_setup_wizard", return_value=True),
        ):
            repl.prompt.prompt.side_effect = [EOFError]
            result = repl.run()

        # "Continuing with default settings..." should NOT appear
        for c in ui["print_muted"].call_args_list:
            if c.args:
                assert "Continuing" not in str(c.args[0])
        assert result == 0

    def test_not_first_run_skips_wizard(self, make_repl):
        """When not first run, wizard is not invoked."""
        repl, ui = make_repl

        repl._show_banner = MagicMock()
        repl._show_startup_notices = MagicMock()
        repl._maybe_show_sponsor_tip = MagicMock()

        with (
            patch(f"{MODULE}.is_first_run", return_value=False),
            patch(f"{MODULE}.run_setup_wizard") as mock_wizard,
        ):
            repl.prompt.prompt.side_effect = [EOFError]
            repl.run()

        mock_wizard.assert_not_called()

    def test_startup_calls_all_phases(self, make_repl):
        """Banner, notices, and sponsor tip are all called during startup."""
        repl, ui = make_repl

        repl._show_banner = MagicMock()
        repl._show_startup_notices = MagicMock()
        repl._maybe_show_sponsor_tip = MagicMock()

        with patch(f"{MODULE}.is_first_run", return_value=False):
            repl.prompt.prompt.side_effect = [EOFError]
            repl.run()

        repl._show_banner.assert_called_once()
        repl._show_startup_notices.assert_called_once()
        repl._maybe_show_sponsor_tip.assert_called_once()


# =============================================================================
# REPL.run - multiple iterations
# =============================================================================


class TestREPLMultipleIterations:
    def test_multiple_commands_processed_in_order(self, make_repl, mock_engine, mock_session):
        from elle.cli.engine import EngineAction

        repl, ui = make_repl

        result1 = MagicMock(output="first", success=True, action=EngineAction.CONTINUE, session=mock_session)
        result2 = MagicMock(output="second", success=True, action=EngineAction.CONTINUE, session=mock_session)
        mock_engine.process.side_effect = [result1, result2]

        repl.prompt.prompt.side_effect = ["cmd1", "cmd2", EOFError]
        repl.run()

        assert mock_engine.process.call_count == 2
        mock_engine.process.assert_any_call("cmd1", mock_session)
        mock_engine.process.assert_any_call("cmd2", mock_session)

    def test_keyboard_interrupt_then_normal_input(self, make_repl, mock_engine, mock_session):
        from elle.cli.engine import EngineAction

        repl, ui = make_repl

        engine_result = MagicMock(output="ok", success=True, action=EngineAction.CONTINUE, session=mock_session)
        mock_engine.process.return_value = engine_result

        repl.prompt.prompt.side_effect = [KeyboardInterrupt, "hello", EOFError]
        result = repl.run()

        assert result == 0
        mock_engine.process.assert_called_once_with("hello", mock_session)

    def test_blank_then_command_then_exit(self, make_repl, mock_engine, mock_session):
        from elle.cli.engine import EngineAction

        repl, ui = make_repl

        exit_result = MagicMock(output="bye", success=True, action=EngineAction.EXIT, session=mock_session)
        mock_engine.process.return_value = exit_result

        repl.prompt.prompt.side_effect = ["", "  ", "quit"]
        result = repl.run()

        # Only the non-blank "quit" should reach the engine
        mock_engine.process.assert_called_once_with("quit", mock_session)
        assert result == 0
