from __future__ import annotations

"""Tests for the ELLE TUI MainScreen (elle.cli.tui.screens.main).

Every external dependency (Textual widgets, EngineBridge, LLM, httpx, daemon
stores) is mocked so these tests run without any services or databases.
"""

import sys
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from elle.cli.tui.screens.main import MainScreen

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_screen() -> MainScreen:
    """Create a MainScreen instance bypassing Screen.__init__."""
    screen = MainScreen.__new__(MainScreen)
    screen.bridge = None
    screen._current_bubble = None
    screen._progress_panel = None
    screen._streaming_bubble = None
    return screen


def _make_mock_app(width: int = 120) -> MagicMock:
    """Return a mock Textual App with a size property."""
    app = MagicMock()
    app.size = MagicMock(width=width)
    return app


def _query_router(
    mock_conversation: MagicMock,
    mock_elle_input: MagicMock,
    mock_header: MagicMock | None = None,
    mock_sidebar: MagicMock | None = None,
):
    """Build a callable that dispatches query_one by widget class name.

    The source code calls self.query_one(ConversationView),
    self.query_one(ElleInput), self.query_one(StatusHeader), etc.
    """

    def _route(cls, *_a, **_kw):
        name = getattr(cls, "__name__", str(cls))
        if name == "ConversationView":
            return mock_conversation
        if name == "ElleInput":
            return mock_elle_input
        if name == "StatusHeader" and mock_header is not None:
            return mock_header
        if name == "SystemSidebar" and mock_sidebar is not None:
            return mock_sidebar
        return MagicMock()

    return _route


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def screen():
    """Return a fresh MainScreen with mocked internals."""
    return _make_screen()


@pytest.fixture()
def mock_conversation():
    """Return a MagicMock standing in for ConversationView."""
    conv = MagicMock()
    bubble = MagicMock()
    conv.add_elle_message.return_value = bubble
    conv.add_user_message.return_value = bubble
    return conv


@pytest.fixture()
def mock_status_header():
    """Return a MagicMock standing in for StatusHeader."""
    return MagicMock()


@pytest.fixture()
def mock_elle_input():
    """Return a MagicMock standing in for ElleInput."""
    return MagicMock()


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestMainScreenInit:
    """Tests for MainScreen.__init__."""

    def test_default_attributes(self):
        """All internal pointers start as None."""
        s = _make_screen()
        assert s.bridge is None
        assert s._current_bubble is None
        assert s._progress_panel is None
        assert s._streaming_bubble is None

    def test_bindings_defined(self):
        """BINDINGS list contains the clear-conversation binding."""
        assert any(b[0] == "ctrl+l" for b in MainScreen.BINDINGS)

    def test_default_css_not_empty(self):
        """DEFAULT_CSS should contain layout directive."""
        assert "vertical" in MainScreen.DEFAULT_CSS


# ---------------------------------------------------------------------------
# compose()
# ---------------------------------------------------------------------------


class TestCompose:
    """Tests for MainScreen.compose (widget tree construction)."""

    @patch("elle.cli.tui.screens.main.Footer")
    @patch("elle.cli.tui.screens.main.ElleInput")
    @patch("elle.cli.tui.screens.main.ConversationView")
    @patch("elle.cli.tui.screens.main.SystemSidebar")
    @patch("elle.cli.tui.screens.main.StatusHeader")
    @patch("elle.cli.tui.screens.main.BannerWidget")
    def test_compose_yields_widgets(
        self,
        MockBanner,
        MockStatusHeader,
        MockSidebar,
        MockConversation,
        MockInput,
        MockFooter,
    ):
        """compose() yields StatusHeader, SystemSidebar, ConversationView,
        ElleInput, and Footer in order."""
        s = _make_screen()
        with patch.object(type(s), "app", new_callable=PropertyMock, return_value=_make_mock_app()):
            widgets = list(s.compose())

        assert len(widgets) == 5
        MockStatusHeader.assert_called_once()
        MockSidebar.assert_called_once()
        MockConversation.assert_called_once()
        MockInput.assert_called_once()
        MockFooter.assert_called_once()

    @patch("elle.cli.tui.screens.main.Footer")
    @patch("elle.cli.tui.screens.main.ElleInput")
    @patch("elle.cli.tui.screens.main.ConversationView")
    @patch("elle.cli.tui.screens.main.SystemSidebar")
    @patch("elle.cli.tui.screens.main.StatusHeader")
    @patch("elle.cli.tui.screens.main.BannerWidget")
    def test_compose_mounts_banner_on_conversation(
        self,
        MockBanner,
        MockStatusHeader,
        MockSidebar,
        MockConversation,
        MockInput,
        MockFooter,
    ):
        """compose() mounts a BannerWidget onto the ConversationView."""
        s = _make_screen()
        with patch.object(type(s), "app", new_callable=PropertyMock, return_value=_make_mock_app(width=100)):
            list(s.compose())

        conv_instance = MockConversation.return_value
        conv_instance.mount.assert_called_once()
        MockBanner.assert_called_once_with(width=100)

    @patch("elle.cli.tui.screens.main.Footer")
    @patch("elle.cli.tui.screens.main.ElleInput")
    @patch("elle.cli.tui.screens.main.ConversationView")
    @patch("elle.cli.tui.screens.main.SystemSidebar")
    @patch("elle.cli.tui.screens.main.StatusHeader")
    @patch("elle.cli.tui.screens.main.BannerWidget")
    def test_compose_uses_default_width_when_no_app(
        self,
        MockBanner,
        MockStatusHeader,
        MockSidebar,
        MockConversation,
        MockInput,
        MockFooter,
    ):
        """compose() falls back to width 80 when self.app is falsy."""
        s = _make_screen()
        with patch.object(type(s), "app", new_callable=PropertyMock, return_value=None):
            list(s.compose())

        MockBanner.assert_called_once_with(width=80)


# ---------------------------------------------------------------------------
# on_mount / _refresh_status
# ---------------------------------------------------------------------------


class TestOnMount:
    """Tests for MainScreen.on_mount and _refresh_status."""

    @patch("elle.cli.tui.screens.main.EngineBridge")
    def test_on_mount_creates_bridge(self, MockBridge, screen):
        """on_mount creates an EngineBridge bound to the screen."""
        screen.query_one = MagicMock(return_value=MagicMock())
        screen._get_model_name = MagicMock(return_value="qwen")
        screen._get_daemon_status = MagicMock(return_value=("running", 8377))
        screen._get_open_incidents = MagicMock(return_value=0)

        screen.on_mount()

        MockBridge.assert_called_once_with(screen)
        assert screen.bridge is MockBridge.return_value

    @patch("elle.cli.tui.screens.main.EngineBridge")
    def test_on_mount_refreshes_status(self, MockBridge, screen):
        """on_mount calls _refresh_status which queries header."""
        header = MagicMock()
        screen.query_one = MagicMock(return_value=header)
        screen._get_model_name = MagicMock(return_value="model-x")
        screen._get_daemon_status = MagicMock(return_value=("stopped", None))
        screen._get_open_incidents = MagicMock(return_value=5)

        screen.on_mount()

        header.refresh_status.assert_called_once_with(
            model="model-x",
            daemon="stopped",
            incidents=5,
        )


# ---------------------------------------------------------------------------
# _get_model_name
# ---------------------------------------------------------------------------


class TestGetModelName:
    """Tests for MainScreen._get_model_name."""

    def test_returns_model_string_when_available(self, screen):
        """Returns str(llm.model) when llm.is_available() is True."""
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = True
        mock_llm.model = "qwen2.5:7b"

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            result = screen._get_model_name()

        assert result == "qwen2.5:7b"

    def test_returns_none_when_not_available(self, screen):
        """Returns None when llm.is_available() is False."""
        mock_llm = MagicMock()
        mock_llm.is_available.return_value = False

        with patch("elle.rag.llm.get_llm", return_value=mock_llm):
            result = screen._get_model_name()

        assert result is None

    def test_returns_none_on_exception(self, screen):
        """Returns None when get_llm raises."""
        with patch("elle.rag.llm.get_llm", side_effect=RuntimeError("boom")):
            result = screen._get_model_name()

        assert result is None


# ---------------------------------------------------------------------------
# _get_daemon_status
# ---------------------------------------------------------------------------


class TestGetDaemonStatus:
    """Tests for MainScreen._get_daemon_status."""

    def test_running_when_health_200(self, screen):
        """Returns ('running', 8377) when /health responds 200."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch.dict(sys.modules, {"httpx": MagicMock()}):
            httpx_mod = sys.modules["httpx"]
            httpx_mod.get.return_value = mock_resp
            result = screen._get_daemon_status()

        assert result == ("running", 8377)

    def test_stopped_on_non_200(self, screen):
        """Returns ('stopped', None) when /health returns non-200."""
        mock_resp = MagicMock()
        mock_resp.status_code = 503

        with patch.dict(sys.modules, {"httpx": MagicMock()}):
            httpx_mod = sys.modules["httpx"]
            httpx_mod.get.return_value = mock_resp
            result = screen._get_daemon_status()

        assert result == ("stopped", None)

    def test_stopped_on_connection_error(self, screen):
        """Returns ('stopped', None) when httpx.get raises."""
        with patch.dict(sys.modules, {"httpx": MagicMock()}):
            httpx_mod = sys.modules["httpx"]
            httpx_mod.get.side_effect = ConnectionError("refused")
            result = screen._get_daemon_status()

        assert result == ("stopped", None)


# ---------------------------------------------------------------------------
# _get_open_incidents
# ---------------------------------------------------------------------------


class TestGetOpenIncidents:
    """Tests for MainScreen._get_open_incidents."""

    def test_returns_count(self, screen):
        """Returns the count from get_incident_count."""
        with patch(
            "elle.daemon.incidents.store.get_incident_count",
            return_value=7,
        ):
            result = screen._get_open_incidents()

        assert result == 7

    def test_returns_zero_on_exception(self, screen):
        """Returns 0 when get_incident_count raises."""
        with patch(
            "elle.daemon.incidents.store.get_incident_count",
            side_effect=RuntimeError("db locked"),
        ):
            result = screen._get_open_incidents()

        assert result == 0


# ---------------------------------------------------------------------------
# on_input_submitted
# ---------------------------------------------------------------------------


class TestOnInputSubmitted:
    """Tests for MainScreen.on_input_submitted."""

    def test_empty_input_ignored(self, screen):
        """Empty or whitespace-only input is silently ignored."""
        event = MagicMock()
        event.value = "   "

        screen.on_input_submitted(event)

        assert screen.bridge is None

    def test_no_bridge_does_nothing(self, screen):
        """When bridge is None, nothing happens even with valid input."""
        event = MagicMock()
        event.value = "hello"
        screen.bridge = None

        screen.on_input_submitted(event)

    def test_submits_to_bridge(self, screen, mock_conversation):
        """Valid input adds a user message and submits to bridge."""
        bridge = MagicMock()
        screen.bridge = bridge
        screen.query_one = MagicMock(return_value=mock_conversation)

        event = MagicMock()
        event.value = "check disk"

        screen.on_input_submitted(event)

        mock_conversation.add_user_message.assert_called_once_with("check disk")
        bridge.submit.assert_called_once_with("check disk")

    def test_input_is_stripped(self, screen, mock_conversation):
        """Leading/trailing whitespace is stripped before processing."""
        bridge = MagicMock()
        screen.bridge = bridge
        screen.query_one = MagicMock(return_value=mock_conversation)

        event = MagicMock()
        event.value = "  ls -la  "

        screen.on_input_submitted(event)

        mock_conversation.add_user_message.assert_called_once_with("ls -la")
        bridge.submit.assert_called_once_with("ls -la")


# ---------------------------------------------------------------------------
# on_engine_started
# ---------------------------------------------------------------------------


class TestOnEngineStarted:
    """Tests for MainScreen.on_engine_started."""

    def test_creates_placeholder_bubble(self, screen, mock_conversation):
        """Creates an elle message bubble with '...' placeholder."""
        screen.query_one = MagicMock(return_value=mock_conversation)
        bubble = MagicMock()
        mock_conversation.add_elle_message.return_value = bubble

        event = MagicMock()
        screen.on_engine_started(event)

        mock_conversation.add_elle_message.assert_called_once_with("...")
        assert screen._current_bubble is bubble
        assert screen._streaming_bubble is None
        assert screen._progress_panel is None


# ---------------------------------------------------------------------------
# on_engine_completed
# ---------------------------------------------------------------------------


class TestOnEngineCompleted:
    """Tests for MainScreen.on_engine_completed."""

    def test_exit_action_exits_app(self, screen):
        """action='exit' calls self.app.exit()."""
        mock_app = _make_mock_app()
        with patch.object(type(screen), "app", new_callable=PropertyMock, return_value=mock_app):
            event = MagicMock()
            event.action = "exit"
            screen.on_engine_completed(event)

        mock_app.exit.assert_called_once()

    def test_clear_action_clears_conversation(self, screen):
        """action='clear' calls action_clear_conversation."""
        screen.action_clear_conversation = MagicMock()
        event = MagicMock()
        event.action = "clear"

        screen.on_engine_completed(event)

        screen.action_clear_conversation.assert_called_once()

    def test_finishes_progress_panel(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """Progress panel is finished and cleared when present."""
        progress = MagicMock()
        screen._progress_panel = progress
        screen._streaming_bubble = MagicMock()

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.action = "continue"
        event.output = ""

        screen.on_engine_completed(event)

        progress.finish.assert_called_once()
        assert screen._progress_panel is None

    def test_streaming_bubble_cleared(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """When streaming bubble exists, both bubble refs are cleared."""
        screen._streaming_bubble = MagicMock()
        screen._current_bubble = MagicMock()

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.action = "continue"
        event.output = "result"

        screen.on_engine_completed(event)

        assert screen._streaming_bubble is None
        assert screen._current_bubble is None

    def test_current_bubble_gets_output(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """When current_bubble exists (no streaming), output is set on it."""
        bubble = MagicMock()
        screen._current_bubble = bubble
        screen._streaming_bubble = None

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.action = "continue"
        event.output = "hello world"

        screen.on_engine_completed(event)

        bubble.set_content.assert_called_once_with("hello world")
        assert screen._current_bubble is None

    def test_no_bubble_adds_new_message(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """When no bubble exists, a new elle message is added."""
        screen._current_bubble = None
        screen._streaming_bubble = None

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.action = "continue"
        event.output = "new output"

        screen.on_engine_completed(event)

        mock_conversation.add_elle_message.assert_called_once_with("new output")

    def test_scrolls_to_bottom_and_refocuses(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """After completion, conversation scrolls to end and input is focused."""
        screen._current_bubble = None
        screen._streaming_bubble = None

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.action = "continue"
        event.output = "done"

        screen.on_engine_completed(event)

        mock_conversation.scroll_end.assert_called_once_with(animate=False)
        mock_elle_input.focus.assert_called_once()

    def test_no_output_no_bubble_no_crash(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """When output is empty and no bubbles exist, no error occurs."""
        screen._current_bubble = None
        screen._streaming_bubble = None

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.action = "continue"
        event.output = ""

        screen.on_engine_completed(event)

        mock_conversation.add_elle_message.assert_not_called()
        mock_conversation.scroll_end.assert_called_once()

    def test_current_bubble_no_output_not_set(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """When current_bubble exists but output is falsy, set_content is
        skipped because line 166 tests both conditions with ``and``."""
        bubble = MagicMock()
        screen._current_bubble = bubble
        screen._streaming_bubble = None

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.action = "continue"
        event.output = ""

        screen.on_engine_completed(event)

        bubble.set_content.assert_not_called()


# ---------------------------------------------------------------------------
# on_engine_error
# ---------------------------------------------------------------------------


class TestOnEngineError:
    """Tests for MainScreen.on_engine_error."""

    def test_error_on_existing_bubble(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """Sets error content on current bubble if present."""
        bubble = MagicMock()
        screen._current_bubble = bubble
        screen._streaming_bubble = MagicMock()

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.error = "LLM timeout"

        screen.on_engine_error(event)

        bubble.set_content.assert_called_once_with("[red]Error: LLM timeout[/red]")
        assert screen._current_bubble is None
        assert screen._streaming_bubble is None
        mock_elle_input.focus.assert_called_once()

    def test_error_no_bubble_adds_message(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """Adds a new error message when no current bubble."""
        screen._current_bubble = None

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.error = "network error"

        screen.on_engine_error(event)

        mock_conversation.add_elle_message.assert_called_once_with(
            "[red]Error: network error[/red]",
        )

    def test_progress_panel_finished_on_error(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """Progress panel is finished when an error occurs."""
        progress = MagicMock()
        screen._progress_panel = progress
        screen._current_bubble = MagicMock()

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.error = "fail"

        screen.on_engine_error(event)

        progress.finish.assert_called_once()
        assert screen._progress_panel is None

    def test_no_progress_panel_no_crash(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """Works fine when there is no progress panel."""
        screen._progress_panel = None
        screen._current_bubble = MagicMock()

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.error = "oops"

        screen.on_engine_error(event)


# ---------------------------------------------------------------------------
# on_agent_stage_changed
# ---------------------------------------------------------------------------


class TestOnAgentStageChanged:
    """Tests for MainScreen.on_agent_stage_changed."""

    @patch("elle.cli.tui.screens.main.AgentProgressPanel")
    def test_creates_progress_panel(
        self,
        MockPanel,
        screen,
        mock_conversation,
    ):
        """Creates a progress panel on the first stage change."""
        screen.query_one = MagicMock(return_value=mock_conversation)

        event = MagicMock()
        event.stage = "retrieve"
        event.description = "Searching documentation"

        screen.on_agent_stage_changed(event)

        MockPanel.assert_called_once()
        mock_conversation.mount.assert_called_once_with(MockPanel.return_value)
        assert screen._progress_panel is MockPanel.return_value

    @patch("elle.cli.tui.screens.main.AgentProgressPanel")
    def test_removes_placeholder_bubble(
        self,
        MockPanel,
        screen,
        mock_conversation,
    ):
        """Removes the '...' placeholder bubble when progress starts."""
        bubble = MagicMock()
        screen._current_bubble = bubble
        screen.query_one = MagicMock(return_value=mock_conversation)

        event = MagicMock()
        event.stage = "retrieve"
        event.description = "desc"

        screen.on_agent_stage_changed(event)

        bubble.remove.assert_called_once()
        assert screen._current_bubble is None

    @patch("elle.cli.tui.screens.main.AgentProgressPanel")
    def test_adds_stage_and_scrolls(
        self,
        MockPanel,
        screen,
        mock_conversation,
    ):
        """Adds the stage to the panel and scrolls to bottom."""
        screen.query_one = MagicMock(return_value=mock_conversation)

        event = MagicMock()
        event.stage = "hypothesize"
        event.description = "Forming hypothesis"

        screen.on_agent_stage_changed(event)

        MockPanel.return_value.add_stage.assert_called_once_with(
            "hypothesize",
            "Forming hypothesis",
        )
        mock_conversation.scroll_end.assert_called_once_with(animate=False)

    def test_reuses_existing_progress_panel(self, screen, mock_conversation):
        """Does not create a new panel when one already exists."""
        existing_panel = MagicMock()
        screen._progress_panel = existing_panel
        screen.query_one = MagicMock(return_value=mock_conversation)

        event = MagicMock()
        event.stage = "act"
        event.description = "Executing capability"

        screen.on_agent_stage_changed(event)

        mock_conversation.mount.assert_not_called()
        existing_panel.add_stage.assert_called_once_with(
            "act",
            "Executing capability",
        )

    @patch("elle.cli.tui.screens.main.AgentProgressPanel")
    def test_no_current_bubble_no_remove(
        self,
        MockPanel,
        screen,
        mock_conversation,
    ):
        """When _current_bubble is None, remove() is not called."""
        screen._current_bubble = None
        screen.query_one = MagicMock(return_value=mock_conversation)

        event = MagicMock()
        event.stage = "verify"
        event.description = "Verifying outcome"

        screen.on_agent_stage_changed(event)

        # No bubble to remove -- just ensure no exception


# ---------------------------------------------------------------------------
# on_agent_token_streamed
# ---------------------------------------------------------------------------


class TestOnAgentTokenStreamed:
    """Tests for MainScreen.on_agent_token_streamed."""

    def test_creates_streaming_bubble(self, screen, mock_conversation):
        """Creates a new streaming bubble on first token."""
        screen._streaming_bubble = None
        bubble = MagicMock()
        mock_conversation.add_elle_message.return_value = bubble
        screen.query_one = MagicMock(return_value=mock_conversation)

        event = MagicMock()
        event.token = "Hello"

        screen.on_agent_token_streamed(event)

        mock_conversation.add_elle_message.assert_called_once_with("")
        assert screen._streaming_bubble is bubble
        bubble.append_text.assert_called_once_with("Hello")

    def test_appends_to_existing_bubble(self, screen, mock_conversation):
        """Appends token to existing streaming bubble."""
        bubble = MagicMock()
        screen._streaming_bubble = bubble
        screen.query_one = MagicMock(return_value=mock_conversation)

        event = MagicMock()
        event.token = " world"

        screen.on_agent_token_streamed(event)

        mock_conversation.add_elle_message.assert_not_called()
        bubble.append_text.assert_called_once_with(" world")

    def test_scrolls_to_end(self, screen, mock_conversation):
        """Scrolls conversation to bottom after each token."""
        screen._streaming_bubble = MagicMock()
        screen.query_one = MagicMock(return_value=mock_conversation)

        event = MagicMock()
        event.token = "x"

        screen.on_agent_token_streamed(event)

        mock_conversation.scroll_end.assert_called_once_with(animate=False)


# ---------------------------------------------------------------------------
# toggle_sidebar
# ---------------------------------------------------------------------------


class TestToggleSidebar:
    """Tests for MainScreen.toggle_sidebar."""

    def test_toggle_calls_sidebar(self, screen):
        """toggle_sidebar queries SystemSidebar and calls toggle()."""
        sidebar = MagicMock()
        screen.query_one = MagicMock(return_value=sidebar)

        screen.toggle_sidebar()

        sidebar.toggle.assert_called_once()


# ---------------------------------------------------------------------------
# action_clear_conversation
# ---------------------------------------------------------------------------


class TestActionClearConversation:
    """Tests for MainScreen.action_clear_conversation."""

    @patch("elle.cli.tui.screens.main.BannerWidget")
    def test_clears_and_remounts_banner(
        self,
        MockBanner,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """Clears conversation children and mounts a fresh banner."""
        with patch.object(type(screen), "app", new_callable=PropertyMock, return_value=_make_mock_app(width=100)):
            screen.query_one = MagicMock(
                side_effect=_query_router(mock_conversation, mock_elle_input),
            )
            screen.action_clear_conversation()

        mock_conversation.remove_children.assert_called_once()
        MockBanner.assert_called_once_with(width=100)
        mock_conversation.mount.assert_called_once()

    @patch("elle.cli.tui.screens.main.BannerWidget")
    def test_resets_all_pointers(
        self,
        MockBanner,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """All bubble/panel pointers are reset to None."""
        screen._current_bubble = MagicMock()
        screen._streaming_bubble = MagicMock()
        screen._progress_panel = MagicMock()

        with patch.object(type(screen), "app", new_callable=PropertyMock, return_value=_make_mock_app()):
            screen.query_one = MagicMock(
                side_effect=_query_router(mock_conversation, mock_elle_input),
            )
            screen.action_clear_conversation()

        assert screen._current_bubble is None
        assert screen._streaming_bubble is None
        assert screen._progress_panel is None

    @patch("elle.cli.tui.screens.main.BannerWidget")
    def test_refocuses_input(
        self,
        MockBanner,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """Input widget is focused after clearing."""
        with patch.object(type(screen), "app", new_callable=PropertyMock, return_value=_make_mock_app()):
            screen.query_one = MagicMock(
                side_effect=_query_router(mock_conversation, mock_elle_input),
            )
            screen.action_clear_conversation()

        mock_elle_input.focus.assert_called_once()


# ---------------------------------------------------------------------------
# _refresh_status integration
# ---------------------------------------------------------------------------


class TestRefreshStatus:
    """Tests for _refresh_status with various state combinations."""

    def test_all_healthy(self, screen):
        """Status refreshed with model, running daemon, zero incidents."""
        header = MagicMock()
        screen.query_one = MagicMock(return_value=header)
        screen._get_model_name = MagicMock(return_value="qwen2.5:7b")
        screen._get_daemon_status = MagicMock(return_value=("running", 8377))
        screen._get_open_incidents = MagicMock(return_value=0)

        screen._refresh_status()

        header.refresh_status.assert_called_once_with(
            model="qwen2.5:7b",
            daemon="running",
            incidents=0,
        )

    def test_degraded_state(self, screen):
        """Status refreshed with no model, stopped daemon, incidents."""
        header = MagicMock()
        screen.query_one = MagicMock(return_value=header)
        screen._get_model_name = MagicMock(return_value=None)
        screen._get_daemon_status = MagicMock(return_value=("stopped", None))
        screen._get_open_incidents = MagicMock(return_value=3)

        screen._refresh_status()

        header.refresh_status.assert_called_once_with(
            model=None,
            daemon="stopped",
            incidents=3,
        )


# ---------------------------------------------------------------------------
# Edge cases / full lifecycle
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Additional edge cases for full line coverage."""

    def test_engine_completed_streaming_then_normal_output(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """After streaming finishes, a subsequent normal completion works."""
        screen._streaming_bubble = MagicMock()
        screen._current_bubble = MagicMock()
        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event1 = MagicMock()
        event1.action = "continue"
        event1.output = "stream done"
        screen.on_engine_completed(event1)

        assert screen._streaming_bubble is None
        assert screen._current_bubble is None

        # Second: normal completion (no bubbles)
        mock_conversation.add_elle_message.reset_mock()
        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event2 = MagicMock()
        event2.action = "continue"
        event2.output = "normal output"
        screen.on_engine_completed(event2)

        mock_conversation.add_elle_message.assert_called_with("normal output")

    def test_error_clears_streaming_bubble(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """on_engine_error clears _streaming_bubble."""
        screen._current_bubble = MagicMock()
        screen._streaming_bubble = MagicMock()
        screen._progress_panel = None

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.error = "timeout"

        screen.on_engine_error(event)

        assert screen._streaming_bubble is None

    def test_multiple_stage_changes_reuse_panel(
        self,
        screen,
        mock_conversation,
    ):
        """Multiple stage changes reuse the same progress panel."""
        panel = MagicMock()
        screen._progress_panel = panel
        screen.query_one = MagicMock(return_value=mock_conversation)

        for stage, desc in [
            ("retrieve", "Searching"),
            ("hypothesize", "Thinking"),
            ("act", "Doing"),
        ]:
            event = MagicMock()
            event.stage = stage
            event.description = desc
            screen.on_agent_stage_changed(event)

        assert panel.add_stage.call_count == 3

    def test_token_stream_then_more_tokens(self, screen, mock_conversation):
        """Streaming multiple tokens appends to the same bubble."""
        bubble = MagicMock()
        mock_conversation.add_elle_message.return_value = bubble
        screen.query_one = MagicMock(return_value=mock_conversation)

        tokens = ["Hello", " ", "world", "!"]
        for token in tokens:
            event = MagicMock()
            event.token = token
            screen.on_agent_token_streamed(event)

        mock_conversation.add_elle_message.assert_called_once_with("")
        assert bubble.append_text.call_count == 4

    def test_engine_completed_with_progress_and_no_streaming(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """Progress panel finishes, current bubble gets output, no streaming."""
        progress = MagicMock()
        bubble = MagicMock()
        screen._progress_panel = progress
        screen._current_bubble = bubble
        screen._streaming_bubble = None

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.action = "continue"
        event.output = "final answer"

        screen.on_engine_completed(event)

        progress.finish.assert_called_once()
        bubble.set_content.assert_called_once_with("final answer")
        assert screen._current_bubble is None
        assert screen._progress_panel is None

    def test_engine_error_no_bubble_focuses_input(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """on_engine_error without current_bubble still focuses input."""
        screen._current_bubble = None
        screen._streaming_bubble = None
        screen._progress_panel = None

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.error = "unexpected"

        screen.on_engine_error(event)

        mock_elle_input.focus.assert_called_once()

    def test_engine_completed_no_progress_no_streaming_no_bubble_empty_output(
        self,
        screen,
        mock_conversation,
        mock_elle_input,
    ):
        """Full no-op path: no progress, no streaming, no bubble, empty output."""
        screen._progress_panel = None
        screen._streaming_bubble = None
        screen._current_bubble = None

        screen.query_one = MagicMock(
            side_effect=_query_router(mock_conversation, mock_elle_input),
        )

        event = MagicMock()
        event.action = "continue"
        event.output = ""

        screen.on_engine_completed(event)

        mock_conversation.scroll_end.assert_called_once()
        mock_elle_input.focus.assert_called_once()
        mock_conversation.add_elle_message.assert_not_called()
