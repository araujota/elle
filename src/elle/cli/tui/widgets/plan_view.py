"""Plan view widget for the ELLE TUI.

Renders a plan using the existing panels.plan_panel() function.
"""

from __future__ import annotations

from typing import Any

from rich.console import RenderableType
from textual.widgets import Static

from elle.cli.ui.panels import plan_panel


class PlanView(Static):
    """Widget that renders a plan panel.

    Wraps panels.plan_panel() to reuse the existing Rich rendering.
    Shows steps with risk badges, privilege indicators, and status.
    """

    DEFAULT_CSS = """
    PlanView {
        width: 1fr;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, plan_data: dict[str, Any], **kwargs: object) -> None:
        """Initialize with plan data.

        Args:
            plan_data: Dict with keys: title, explanation, steps, risks,
                      rollback_steps, man_citations, incident_citations, confidence.
        """
        super().__init__(**kwargs)
        self._plan_data = plan_data

    def render(self) -> RenderableType:
        """Render the plan panel."""
        return plan_panel(
            title=self._plan_data.get("title", "Plan"),
            explanation=self._plan_data.get("explanation", ""),
            steps=self._plan_data.get("steps", []),
            risks=self._plan_data.get("risks"),
            rollback_steps=self._plan_data.get("rollback_steps"),
            man_citations=self._plan_data.get("man_citations"),
            incident_citations=self._plan_data.get("incident_citations"),
            confidence=self._plan_data.get("confidence"),
        )
