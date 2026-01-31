"""Engine bridge for the ELLE TUI.

Wraps the Engine class in Textual workers so that command processing
runs off the UI thread. All results flow back to the UI via
post_message() — Textual's only thread-safe API.

For agent loop commands, uses async workers with progress/stream
callbacks that post Textual messages for live UI updates.
"""

from __future__ import annotations

import logging

from textual.widget import Widget
from textual.worker import Worker, WorkerState

from elle.cli.engine import Engine, EngineAction, EngineResult, get_engine
from elle.cli.tui.messages import (
    AgentStageChanged,
    AgentTokenStreamed,
    EngineCompleted,
    EngineError,
    EngineStarted,
)
from elle.common.session import Session, create_session

logger = logging.getLogger(__name__)


class EngineBridge:
    """Bridge between the TUI and the ELLE Engine.

    Runs engine commands in threaded workers and posts results
    back to the TUI via Textual messages.

    For system_question/system_task intents, uses async workers
    to call the agent loop directly with progress callbacks.
    """

    def __init__(self, owner: Widget) -> None:
        """Initialize the bridge.

        Args:
            owner: The widget that owns this bridge (for posting messages
                   and running workers).
        """
        self.owner = owner
        self.engine: Engine = get_engine()
        self.session: Session = create_session()
        self._current_worker: Worker[EngineResult | None] | None = None

    @property
    def is_busy(self) -> bool:
        """Check if a command is currently being processed."""
        if self._current_worker is None:
            return False
        return self._current_worker.state == WorkerState.RUNNING

    def submit(self, command: str) -> None:
        """Submit a command for processing.

        Detects if the command should go through the agent loop
        (with streaming/progress) or through the standard engine.

        Args:
            command: The user command to process.
        """
        if self.is_busy:
            self.owner.post_message(EngineError("A command is already being processed."))
            return

        self.owner.post_message(EngineStarted(command))

        # Check if this should go through the agent loop
        if self._should_use_agent_loop(command):
            self._current_worker = self.owner.run_worker(
                self._run_agent_loop(command),
                exclusive=True,
                group="engine",
            )
        else:
            self._current_worker = self.owner.run_worker(
                self._run_engine(command),
                thread=True,
                exclusive=True,
                group="engine",
            )

    def _should_use_agent_loop(self, command: str) -> bool:
        """Check if the command should use the agent loop.

        Returns True for system_question and system_task intents
        when the agentic loop is enabled.
        """
        try:
            from elle.cli.agentic.loop import is_agentic_loop_enabled

            if not is_agentic_loop_enabled():
                return False

            from elle.cli.terminal.classifier import get_classifier
            from elle.cli.terminal.intent import Intent
            from elle.common.session import Session

            classifier = get_classifier()
            result = classifier.classify(command, Session())
            return result.intent in (Intent.SYSTEM_QUESTION, Intent.SYSTEM_TASK)
        except Exception:
            return False

    def _run_engine(self, command: str) -> EngineResult:
        """Run the engine synchronously (called in worker thread).

        Args:
            command: The user command to process.

        Returns:
            EngineResult from the engine.
        """
        try:
            result = self.engine.process(command, self.session)

            # Update session state
            self.session = result.session

            # Map EngineAction to string for message
            action_map = {
                EngineAction.CONTINUE: "continue",
                EngineAction.EXIT: "exit",
                EngineAction.CLEAR: "clear",
            }
            action = action_map.get(result.action, "continue")

            self.owner.post_message(
                EngineCompleted(
                    output=result.output,
                    success=result.success,
                    action=action,
                )
            )
            return result

        except Exception as e:
            logger.exception("Engine processing failed")
            self.owner.post_message(EngineError(str(e)))
            raise

    async def _run_agent_loop(self, command: str) -> None:
        """Run the agent loop asynchronously with progress callbacks.

        Args:
            command: The user command to process.
        """
        try:
            from elle.cli.agentic.loop import AgenticLoop

            loop = AgenticLoop()

            def stream_cb(token: str) -> None:
                self.owner.post_message(AgentTokenStreamed(token))

            def progress_cb(stage: str, description: str, iteration: int) -> None:
                self.owner.post_message(AgentStageChanged(stage, description, iteration))

            result = await loop.run(
                command,
                stream_callback=stream_cb,
                progress_callback=progress_cb,
            )

            self.owner.post_message(
                EngineCompleted(
                    output=result.response,
                    success=result.success,
                    action="continue",
                )
            )

        except Exception as e:
            logger.exception("Agent loop failed")
            self.owner.post_message(EngineError(str(e)))
