"""Plan approval modal screen for the ELLE TUI.

Shows a full plan via PlanView and lets the user approve or reject it.
The border color reflects the plan's risk level.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Static

from elle.cli.tui.widgets.plan_view import PlanView
from elle.cli.ui.theme import Theme


class PlanApprovalScreen(ModalScreen[bool]):
    """Modal screen for approving or rejecting a plan.

    Returns True if approved, False if rejected.
    """

    DEFAULT_CSS = """
    PlanApprovalScreen {
        align: center middle;
    }

    PlanApprovalScreen > VerticalScroll {
        width: 85%;
        height: 80%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    PlanApprovalScreen .plan-title {
        text-style: bold;
        text-align: center;
        padding: 0 0 1 0;
    }

    PlanApprovalScreen .plan-actions {
        dock: bottom;
        height: 3;
        padding: 1 2;
        align: center middle;
    }

    PlanApprovalScreen .plan-actions Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("y", "approve", "Approve", show=True),
        Binding("n", "reject", "Reject", show=True),
        Binding("escape", "reject", "Cancel"),
    ]

    def __init__(self, plan_data: dict[str, Any]) -> None:
        """Initialize with plan data.

        Args:
            plan_data: Plan dict with title, explanation, steps, risks, etc.
        """
        super().__init__()
        self._plan_data = plan_data

    def compose(self) -> ComposeResult:
        """Compose the plan approval layout."""
        # Determine risk color for the border
        risk = self._plan_data.get("risk", "low")
        risk_color = Theme.risk_color(risk)

        with VerticalScroll():
            yield Static("[bold]Plan Approval Required[/bold]", classes="plan-title")
            yield PlanView(self._plan_data)

            # Risk warning if applicable
            risks = self._plan_data.get("risks", [])
            if risks:
                yield Static(f"\n[{risk_color}]Risks:[/{risk_color}]")
                for r in risks:
                    yield Static(f"  - {r}")

        with Horizontal(classes="plan-actions"):
            yield Button("Approve (y)", variant="success", id="approve")
            yield Button("Reject (n)", variant="error", id="reject")

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "approve":
            self.dismiss(True)
        elif event.button.id == "reject":
            self.dismiss(False)

    def action_approve(self) -> None:
        """Approve the plan."""
        self.dismiss(True)

    def action_reject(self) -> None:
        """Reject the plan."""
        self.dismiss(False)
