"""Custom Textual Message types for the ELLE TUI.

All inter-widget communication flows through these typed messages.
Workers post messages to communicate results back to the UI thread.
"""

from __future__ import annotations

from typing import Any

from textual.message import Message


class EngineStarted(Message):
    """Posted when an engine command begins processing."""

    def __init__(self, command: str) -> None:
        super().__init__()
        self.command = command


class EngineCompleted(Message):
    """Posted when an engine command completes."""

    def __init__(self, output: str, success: bool = True, action: str = "continue") -> None:
        super().__init__()
        self.output = output
        self.success = success
        self.action = action


class EngineError(Message):
    """Posted when an engine command fails with an exception."""

    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error


class AgentStageChanged(Message):
    """Posted when the agent loop enters a new stage."""

    def __init__(self, stage: str, description: str, iteration: int = 0) -> None:
        super().__init__()
        self.stage = stage
        self.description = description
        self.iteration = iteration


class AgentTokenStreamed(Message):
    """Posted when a token is streamed from the agent loop."""

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token


class AgentToolCalled(Message):
    """Posted when the agent loop calls a tool."""

    def __init__(self, tool_name: str, arguments: dict[str, Any], iteration: int = 0) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments
        self.iteration = iteration


class AgentToolResult(Message):
    """Posted when a tool call returns a result."""

    def __init__(self, tool_name: str, success: bool, output_preview: str = "") -> None:
        super().__init__()
        self.tool_name = tool_name
        self.success = success
        self.output_preview = output_preview


class PlanRequiresApproval(Message):
    """Posted when the agent loop needs plan approval."""

    def __init__(self, plan_data: dict[str, Any]) -> None:
        super().__init__()
        self.plan_data = plan_data


class PlanApproved(Message):
    """Posted when the user approves a plan."""

    def __init__(self, plan_id: str) -> None:
        super().__init__()
        self.plan_id = plan_id


class PlanRejected(Message):
    """Posted when the user rejects a plan."""

    def __init__(self, plan_id: str) -> None:
        super().__init__()
        self.plan_id = plan_id


class FixitReady(Message):
    """Posted when fixit data is ready for display."""

    def __init__(self, fixit_data: dict[str, Any]) -> None:
        super().__init__()
        self.fixit_data = fixit_data
