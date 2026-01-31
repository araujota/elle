"""Setup wizard modal screen for the ELLE TUI.

Paginated onboarding wizard that guides the user through initial
configuration. Reuses data models and save/policy logic from
the existing setup module.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Static

from elle.cli.setup.models import (
    CONFIRMATION_INFO,
    FEATURE_INFO,
    LLM_PROVIDER_INFO,
    SAFETY_LEVEL_INFO,
    TELEMETRY_INFO,
    LLMProviderChoice,
    SetupPreferences,
    SetupState,
)
from elle.cli.ui.theme import Colors, Icons

# Total number of wizard steps
_TOTAL_STEPS = 7


class SetupScreen(ModalScreen[bool]):
    """Modal setup wizard screen.

    Paginated wizard with 6 steps. Returns True if setup was
    completed, False if cancelled.
    """

    DEFAULT_CSS = """
    SetupScreen {
        align: center middle;
    }

    SetupScreen > Vertical {
        width: 80%;
        height: 85%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    SetupScreen .wizard-header {
        text-style: bold;
        text-align: center;
        padding: 1 0;
    }

    SetupScreen .step-counter {
        text-align: center;
        color: $text-muted;
        padding: 0 0 1 0;
    }

    SetupScreen .wizard-content {
        height: 1fr;
        padding: 0 2;
    }

    SetupScreen .wizard-nav {
        dock: bottom;
        height: 3;
        padding: 1 2;
        align: center middle;
    }

    SetupScreen .wizard-nav Button {
        margin: 0 1;
    }

    SetupScreen .section-label {
        padding: 1 0 0 0;
        text-style: bold;
    }

    SetupScreen .option-desc {
        padding: 0 0 0 2;
        color: $text-muted;
    }

    SetupScreen .warning-text {
        color: $warning;
        padding: 0 0 0 2;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    current_step: reactive[int] = reactive(1)

    def __init__(self, *, reconfigure: bool = False) -> None:
        """Initialize the setup wizard.

        Args:
            reconfigure: If True, load existing preferences as defaults.
        """
        super().__init__()
        self._reconfigure = reconfigure
        self.prefs = SetupPreferences()

        if reconfigure:
            try:
                from elle.cli.setup.wizard import load_setup_state

                state = load_setup_state()
                if state.preferences:
                    self.prefs = state.preferences
            except Exception:
                pass

    def compose(self) -> ComposeResult:
        """Compose the wizard layout."""
        with Vertical():
            yield Static(
                f"[bold {Colors.ACCENT}]{Icons.SETTINGS} ELLE Setup Wizard[/bold {Colors.ACCENT}]",
                classes="wizard-header",
            )
            yield Static("", id="step-counter", classes="step-counter")

            with VerticalScroll(classes="wizard-content", id="wizard-content"):
                # Content is dynamically populated per step
                yield Static("", id="step-content")

            with Horizontal(classes="wizard-nav"):
                yield Button("Back", id="back", disabled=True)
                yield Button("Next", variant="primary", id="next")
                yield Button("Cancel", id="cancel")

        yield Footer()

    def on_mount(self) -> None:
        """Render the first step."""
        self._render_step()

    def watch_current_step(self, step: int) -> None:
        """React to step changes."""
        self._render_step()

    def _render_step(self) -> None:
        """Render the current wizard step."""
        counter = self.query_one("#step-counter", Static)
        counter.update(f"Step {self.current_step} of {_TOTAL_STEPS}")

        # Update navigation buttons
        back_btn = self.query_one("#back", Button)
        next_btn = self.query_one("#next", Button)
        back_btn.disabled = self.current_step == 1

        if self.current_step == _TOTAL_STEPS:
            next_btn.label = "Confirm"
            next_btn.variant = "success"
        else:
            next_btn.label = "Next"
            next_btn.variant = "primary"

        # Clear and rebuild step content
        content_area = self.query_one("#wizard-content", VerticalScroll)
        old_content = self.query_one("#step-content", Static)
        old_content.remove()

        step_renderers = {
            1: self._step_environment,
            2: self._step_llm_provider,
            3: self._step_safety,
            4: self._step_telemetry,
            5: self._step_features,
            6: self._step_privileges,
            7: self._step_review,
        }

        renderer = step_renderers.get(self.current_step, self._step_environment)
        text = renderer()
        content_area.mount(Static(text, id="step-content"))

    def _step_environment(self) -> str:
        """Step 1: Environment check."""
        lines = [
            "[bold]Environment Check[/bold]\n",
            "Verifying system requirements...\n",
        ]

        # Check Ollama
        try:
            import shutil

            has_ollama = shutil.which("ollama") is not None
            icon = Icons.SUCCESS if has_ollama else Icons.ERROR
            color = Colors.SUCCESS if has_ollama else Colors.ERROR
            lines.append(f"  [{color}]{icon}[/{color}] Ollama: {'found' if has_ollama else 'not found'}")
        except Exception:
            lines.append(f"  [{Colors.WARNING}]{Icons.WARNING}[/{Colors.WARNING}] Ollama: check failed")

        # Check systemd
        try:
            import shutil

            has_systemd = shutil.which("systemctl") is not None
            icon = Icons.SUCCESS if has_systemd else Icons.WARNING
            color = Colors.SUCCESS if has_systemd else Colors.WARNING
            lines.append(f"  [{color}]{icon}[/{color}] systemd: {'available' if has_systemd else 'not found'}")
        except Exception:
            lines.append(f"  [{Colors.MUTED}]{Icons.PENDING}[/{Colors.MUTED}] systemd: check skipped")

        # Python version
        import sys

        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        lines.append(f"  [{Colors.SUCCESS}]{Icons.SUCCESS}[/{Colors.SUCCESS}] Python: {py_ver}")

        lines.append(f"\n[{Colors.MUTED}]Press Next to continue.[/{Colors.MUTED}]")
        return "\n".join(lines)

    def _step_llm_provider(self) -> str:
        """Step 2: LLM provider selection."""
        lines = [
            "[bold]LLM Provider[/bold]\n",
            "Choose how ELLE should run LLM inference:\n",
        ]

        for provider, info in LLM_PROVIDER_INFO.items():
            marker = Icons.CHEVRON if provider == self.prefs.llm_provider else " "
            lines.append(f"  {marker} [{Colors.ACCENT}]{info['name']}[/{Colors.ACCENT}]")
            lines.append(f"    [{Colors.MUTED}]{info['description']}[/{Colors.MUTED}]\n")

        if self.prefs.llm_provider == LLMProviderChoice.REMOTE:
            lines.append("\n[bold]Remote Endpoint[/bold]\n")
            host = self.prefs.llm_remote_host or "(not set)"
            model = self.prefs.llm_remote_model or "gpt-4o"
            lines.append(f"  Host:  [{Colors.ACCENT}]{host}[/{Colors.ACCENT}]")
            lines.append(f"  Model: [{Colors.ACCENT}]{model}[/{Colors.ACCENT}]")
            lines.append(
                f"\n  [{Colors.MUTED}]Set the API key via the ELLE_LLM_API_KEY environment variable.[/{Colors.MUTED}]"
            )
            lines.append(
                f"  [{Colors.MUTED}]Local Ollama will be used as fallback "
                f"when the remote endpoint is unavailable.[/{Colors.MUTED}]"
            )
        else:
            lines.append(
                f"\n  [{Colors.MUTED}]Ensure Ollama is installed and running with a supported model.[/{Colors.MUTED}]"
            )

        return "\n".join(lines)

    def _step_safety(self) -> str:
        """Step 3: Safety settings."""
        lines = [
            "[bold]Safety Settings[/bold]\n",
        ]

        for level, info in SAFETY_LEVEL_INFO.items():
            marker = Icons.CHEVRON if level == self.prefs.safety_level else " "
            lines.append(f"  {marker} [{Colors.ACCENT}]{info['name']}[/{Colors.ACCENT}]")
            lines.append(f"    [{Colors.MUTED}]{info['description']}[/{Colors.MUTED}]\n")

        lines.append("\n[bold]Confirmation Preference[/bold]\n")
        for pref, info in CONFIRMATION_INFO.items():
            marker = Icons.CHEVRON if pref == self.prefs.confirmation_preference else " "
            lines.append(f"  {marker} [{Colors.ACCENT}]{info['name']}[/{Colors.ACCENT}]")
            lines.append(f"    [{Colors.MUTED}]{info['description']}[/{Colors.MUTED}]\n")

        lines.append(
            f"\n  Preview config changes: [{Colors.ACCENT}]{'yes' if self.prefs.require_preview_for_configs else 'no'}[/{Colors.ACCENT}]"
        )
        return "\n".join(lines)

    def _step_telemetry(self) -> str:
        """Step 4: Telemetry sources."""
        lines = [
            "[bold]Telemetry Sources[/bold]\n",
            "Select which system events ELLE should monitor:\n",
        ]

        field_map = {
            "journal": "journal_enabled",
            "kernel": "kernel_enabled",
            "probes": "probes_enabled",
            "ebpf": "ebpf_enabled",
            "docker": "docker_enabled",
        }

        for key, info in TELEMETRY_INFO.items():
            field = field_map.get(key, "")
            enabled = getattr(self.prefs, field, False) if field else False
            icon = Icons.SUCCESS if enabled else Icons.PENDING
            color = Colors.SUCCESS if enabled else Colors.MUTED
            lines.append(f"  [{color}]{icon}[/{color}] {info['name']}")
            lines.append(f"    [{Colors.MUTED}]{info['description']}[/{Colors.MUTED}]\n")

        return "\n".join(lines)

    def _step_features(self) -> str:
        """Step 5: Optional features."""
        lines = [
            "[bold]Optional Features[/bold]\n",
        ]

        field_map = {
            "api": "api_enabled",
            "auto_learn_packages": "auto_learn_packages",
        }

        for key, info in FEATURE_INFO.items():
            field = field_map.get(key, "")
            enabled = getattr(self.prefs, field, False) if field else False
            icon = Icons.SUCCESS if enabled else Icons.PENDING
            color = Colors.SUCCESS if enabled else Colors.MUTED
            lines.append(f"  [{color}]{icon}[/{color}] {info['name']}")
            lines.append(f"    [{Colors.MUTED}]{info['description']}[/{Colors.MUTED}]\n")

        return "\n".join(lines)

    def _step_privileges(self) -> str:
        """Step 6: Privilege configuration."""
        lines = [
            "[bold]Privilege Configuration[/bold]\n",
            "How should ELLE handle privileged operations?\n",
        ]

        from elle.cli.setup.models import PRIVILEGE_LEVEL_INFO

        for level, info in PRIVILEGE_LEVEL_INFO.items():
            marker = Icons.CHEVRON if level == self.prefs.privilege_level else " "
            lines.append(f"  {marker} [{Colors.ACCENT}]{info['name']}[/{Colors.ACCENT}]")
            lines.append(f"    [{Colors.MUTED}]{info['description']}[/{Colors.MUTED}]")
            if "warning" in info:
                lines.append(f"    [{Colors.WARNING}]{Icons.WARNING} {info['warning']}[/{Colors.WARNING}]")
            lines.append("")

        return "\n".join(lines)

    def _step_review(self) -> str:
        """Step 7: Review and confirm."""
        llm_label = "Local Ollama" if self.prefs.llm_provider == LLMProviderChoice.LOCAL else "Remote OpenAI-compatible"
        lines = [
            "[bold]Review Configuration[/bold]\n",
            f"  LLM provider:    [{Colors.ACCENT}]{llm_label}[/{Colors.ACCENT}]",
            f"  Safety level:    [{Colors.ACCENT}]{self.prefs.safety_level.value}[/{Colors.ACCENT}]",
            f"  Confirmations:   [{Colors.ACCENT}]{self.prefs.confirmation_preference.value}[/{Colors.ACCENT}]",
            f"  Config preview:  [{Colors.ACCENT}]{'yes' if self.prefs.require_preview_for_configs else 'no'}[/{Colors.ACCENT}]",
            f"  Privilege level: [{Colors.ACCENT}]{self.prefs.privilege_level.value}[/{Colors.ACCENT}]",
            "",
            "  [bold]Telemetry:[/bold]",
            f"    Journal: {'on' if self.prefs.journal_enabled else 'off'}",
            f"    Kernel:  {'on' if self.prefs.kernel_enabled else 'off'}",
            f"    Probes:  {'on' if self.prefs.probes_enabled else 'off'}",
            f"    eBPF:    {'on' if self.prefs.ebpf_enabled else 'off'}",
            f"    Docker:  {'on' if self.prefs.docker_enabled else 'off'}",
            "",
            "  [bold]Features:[/bold]",
            f"    REST API:       {'on' if self.prefs.api_enabled else 'off'}",
            f"    Auto-learn:     {'on' if self.prefs.auto_learn_packages else 'off'}",
            "",
            f"\n[{Colors.ACCENT}]Press Confirm to save and start ELLE.[/{Colors.ACCENT}]",
        ]
        return "\n".join(lines)

    # ── Navigation ─────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle navigation button presses."""
        if event.button.id == "next":
            if self.current_step >= _TOTAL_STEPS:
                self._save_and_finish()
            else:
                self.current_step += 1
        elif event.button.id == "back":
            if self.current_step > 1:
                self.current_step -= 1
        elif event.button.id == "cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        """Cancel the wizard."""
        self.dismiss(False)

    def _save_and_finish(self) -> None:
        """Save preferences and dismiss."""
        try:
            from datetime import datetime, timezone

            from elle.cli.setup.wizard import generate_policy_file, save_setup_state

            state = SetupState(
                completed=True,
                completed_at=datetime.now(timezone.utc),
                preferences=self.prefs,
            )
            save_setup_state(state)
            generate_policy_file(self.prefs)
        except Exception:
            pass

        self.dismiss(True)
