"""Main screen for the ELLE TUI.

The primary conversation screen where users interact with ELLE.
Composes the status header, conversation view, and input bar.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Input

from elle.cli.tui.bridge import EngineBridge
from elle.cli.tui.messages import (
    AgentStageChanged,
    AgentTokenStreamed,
    EngineCompleted,
    EngineError,
    EngineStarted,
)
from elle.cli.tui.widgets.agent_progress import AgentProgressPanel
from elle.cli.tui.widgets.banner import BannerWidget
from elle.cli.tui.widgets.conversation import ConversationView
from elle.cli.tui.widgets.input_bar import ElleInput
from elle.cli.tui.widgets.message import MessageBubble
from elle.cli.tui.widgets.status_header import StatusHeader
from elle.cli.tui.widgets.system_sidebar import SystemSidebar


class MainScreen(Screen):
    """Primary conversation screen.

    Layout:
        StatusHeader (docked top)
        ConversationView (fills center)
        ElleInput (docked bottom)
        Footer (docked bottom)
    """

    DEFAULT_CSS = """
    MainScreen {
        layout: vertical;
    }
    """

    BINDINGS = [
        ("ctrl+l", "clear_conversation", "Clear"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.bridge: EngineBridge | None = None
        self._current_bubble: MessageBubble | None = None
        self._progress_panel: AgentProgressPanel | None = None
        self._streaming_bubble: MessageBubble | None = None

    def compose(self) -> ComposeResult:
        """Compose the main screen layout."""
        yield StatusHeader()
        yield SystemSidebar()
        conversation = ConversationView()
        conversation.mount(BannerWidget(width=self.app.size.width if self.app else 80))
        yield conversation
        yield ElleInput()
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the engine bridge and collect system status."""
        self.bridge = EngineBridge(self)
        self._refresh_status()

    def _refresh_status(self) -> None:
        """Refresh the status header with current system state."""
        header = self.query_one(StatusHeader)

        model = self._get_model_name()
        daemon, _port = self._get_daemon_status()
        incidents = self._get_open_incidents()

        header.refresh_status(model=model, daemon=daemon, incidents=incidents)

    def _get_model_name(self) -> str | None:
        """Get the current LLM model name."""
        try:
            from elle.rag.llm import get_llm

            llm = get_llm()
            if llm.is_available():
                return str(llm.model)
        except Exception:
            pass
        return None

    def _get_daemon_status(self) -> tuple[str, int | None]:
        """Get daemon status and API port."""
        api_port = 8377
        try:
            import httpx

            response = httpx.get(f"http://localhost:{api_port}/health", timeout=2.0)
            if response.status_code == 200:
                return ("running", api_port)
        except Exception:
            pass
        return ("stopped", None)

    def _get_open_incidents(self) -> int:
        """Get count of open incidents."""
        try:
            from elle.daemon.incidents.store import get_incident_count

            return get_incident_count()
        except Exception:
            return 0

    # ── Input handling ──────────────────────────────────────────────

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user input submission."""
        command = event.value.strip()
        if not command:
            return

        if self.bridge is None:
            return

        # Add user message to conversation
        conversation = self.query_one(ConversationView)
        conversation.add_user_message(command)

        # Submit to engine
        self.bridge.submit(command)

    # ── Engine lifecycle messages ────────────────────────────────────

    @on(EngineStarted)
    def on_engine_started(self, event: EngineStarted) -> None:
        """Handle engine command start — show a placeholder response."""
        conversation = self.query_one(ConversationView)
        self._current_bubble = conversation.add_elle_message("...")
        self._streaming_bubble = None
        self._progress_panel = None

    @on(EngineCompleted)
    def on_engine_completed(self, event: EngineCompleted) -> None:
        """Handle engine command completion."""
        if event.action == "exit":
            self.app.exit()
            return

        if event.action == "clear":
            self.action_clear_conversation()
            return

        # Finalize progress panel
        if self._progress_panel is not None:
            self._progress_panel.finish()
            self._progress_panel = None

        # If we were streaming, the bubble already has content
        if self._streaming_bubble is not None:
            self._streaming_bubble = None
            self._current_bubble = None
        elif self._current_bubble is not None and event.output:
            self._current_bubble.set_content(event.output)
            self._current_bubble = None
        elif event.output:
            conversation = self.query_one(ConversationView)
            conversation.add_elle_message(event.output)

        # Scroll to bottom
        conversation = self.query_one(ConversationView)
        conversation.scroll_end(animate=False)

        # Re-focus input
        self.query_one(ElleInput).focus()

    @on(EngineError)
    def on_engine_error(self, event: EngineError) -> None:
        """Handle engine processing error."""
        # Clean up progress panel
        if self._progress_panel is not None:
            self._progress_panel.finish()
            self._progress_panel = None

        if self._current_bubble is not None:
            self._current_bubble.set_content(f"[red]Error: {event.error}[/red]")
            self._current_bubble = None
        else:
            conversation = self.query_one(ConversationView)
            conversation.add_elle_message(f"[red]Error: {event.error}[/red]")

        self._streaming_bubble = None
        self.query_one(ElleInput).focus()

    # ── Agent loop progress messages ────────────────────────────────

    @on(AgentStageChanged)
    def on_agent_stage_changed(self, event: AgentStageChanged) -> None:
        """Handle agent loop stage transitions."""
        conversation = self.query_one(ConversationView)

        # Create progress panel if not present
        if self._progress_panel is None:
            self._progress_panel = AgentProgressPanel()
            conversation.mount(self._progress_panel)

            # Remove the placeholder "..." bubble
            if self._current_bubble is not None:
                self._current_bubble.remove()
                self._current_bubble = None

        self._progress_panel.add_stage(event.stage, event.description)
        conversation.scroll_end(animate=False)

    @on(AgentTokenStreamed)
    def on_agent_token_streamed(self, event: AgentTokenStreamed) -> None:
        """Handle streaming tokens from the agent loop."""
        conversation = self.query_one(ConversationView)

        # Create streaming bubble if not present
        if self._streaming_bubble is None:
            self._streaming_bubble = conversation.add_elle_message("")

        self._streaming_bubble.append_text(event.token)
        conversation.scroll_end(animate=False)

    # ── Actions ─────────────────────────────────────────────────────

    def toggle_sidebar(self) -> None:
        """Toggle the system sidebar visibility."""
        sidebar = self.query_one(SystemSidebar)
        sidebar.toggle()

    def action_clear_conversation(self) -> None:
        """Clear the conversation view."""
        conversation = self.query_one(ConversationView)
        conversation.remove_children()
        conversation.mount(BannerWidget(width=self.app.size.width))
        self._current_bubble = None
        self._streaming_bubble = None
        self._progress_panel = None
        self.query_one(ElleInput).focus()
