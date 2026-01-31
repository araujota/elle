from __future__ import annotations

"""Tests for the TUI setup wizard screen (elle.cli.tui.screens.setup).

Covers all 7 wizard steps, navigation, reconfigure mode, save/finish,
and cancel handling.  All Textual widgets and external elle modules are
mocked so the tests run without a live TUI or any external service.
"""

import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from elle.cli.setup.models import (
    CONFIRMATION_INFO,
    FEATURE_INFO,
    LLM_PROVIDER_INFO,
    PRIVILEGE_LEVEL_INFO,
    SAFETY_LEVEL_INFO,
    TELEMETRY_INFO,
    ConfirmationPreference,
    LLMProviderChoice,
    PrivilegeLevel,
    SafetyLevel,
    SetupPreferences,
    SetupState,
)
from elle.cli.ui.theme import Icons

# ---------------------------------------------------------------------------
# Helper: build a SetupScreen with the Textual reactive plumbing neutralised
# ---------------------------------------------------------------------------


def _make_screen(reconfigure: bool = False, **pref_overrides):
    """Return a SetupScreen ready for unit-testing.

    Bypasses ``ModalScreen.__init__`` (which needs a running Textual app)
    and seeds the internal attributes that the ``reactive`` descriptor
    expects so that ``screen.current_step = N`` works normally.
    """
    from elle.cli.tui.screens.setup import SetupScreen

    with patch.object(SetupScreen, "__init__", lambda self, **kw: None):
        screen = SetupScreen.__new__(SetupScreen)

    # ------------------------------------------------------------------
    # Satisfy the Textual reactive descriptor
    # ------------------------------------------------------------------
    # ``reactive.__set__`` checks ``hasattr(obj, '_id')`` before doing
    # anything.  Setting ``_id`` makes that guard pass.
    screen._id = "setup-screen"
    # Pre-seed the internal storage so the descriptor skips its lazy-init
    # path (which would try to call ``set_class`` / ``refresh`` etc.).
    screen._reactive_current_step = 1
    # Stubs for methods that the reactive machinery may invoke.
    screen.set_class = MagicMock()
    screen.refresh = MagicMock()
    screen.refresh_bindings = MagicMock()

    # ------------------------------------------------------------------
    # Set up the screen's own state
    # ------------------------------------------------------------------
    screen._reconfigure = reconfigure
    screen.prefs = SetupPreferences(**pref_overrides)
    # Use the internal name directly for the initial value to avoid the
    # descriptor's watcher invocation at construction time.
    screen._reactive_current_step = 1

    # ------------------------------------------------------------------
    # Mock widget tree used by _render_step
    # ------------------------------------------------------------------
    counter_widget = MagicMock(name="counter-widget")
    back_btn = MagicMock(name="back-btn")
    next_btn = MagicMock(name="next-btn")
    content_area = MagicMock(name="content-area")
    step_content = MagicMock(name="step-content")

    def _query_one(selector, cls=None):
        return {
            "#step-counter": counter_widget,
            "#back": back_btn,
            "#next": next_btn,
            "#wizard-content": content_area,
            "#step-content": step_content,
        }[selector]

    screen.query_one = _query_one

    # Stash references for assertions
    screen._mock_counter = counter_widget
    screen._mock_back = back_btn
    screen._mock_next = next_btn
    screen._mock_content_area = content_area
    screen._mock_step_content = step_content

    return screen


# ====================================================================
# Initialization
# ====================================================================


class TestSetupScreenInit:
    """Tests for SetupScreen.__init__."""

    def test_default_init_fresh_prefs(self) -> None:
        """Default construction uses fresh SetupPreferences."""
        screen = _make_screen()
        assert screen._reconfigure is False
        assert screen.prefs.safety_level == SafetyLevel.STANDARD

    def test_reconfigure_loads_saved_state(self) -> None:
        """When reconfigure=True, existing preferences are loaded."""
        saved_prefs = SetupPreferences(
            safety_level=SafetyLevel.CAUTIOUS,
            journal_enabled=False,
        )
        saved_state = SetupState(
            completed=True,
            completed_at=datetime.now(timezone.utc),
            preferences=saved_prefs,
        )

        from elle.cli.tui.screens.setup import SetupScreen

        with (
            patch(
                "elle.cli.setup.wizard.load_setup_state",
                return_value=saved_state,
            ),
            patch(
                "textual.screen.ModalScreen.__init__",
                return_value=None,
            ),
        ):
            screen = SetupScreen(reconfigure=True)

        assert screen._reconfigure is True
        assert screen.prefs.safety_level == SafetyLevel.CAUTIOUS
        assert screen.prefs.journal_enabled is False

    def test_reconfigure_exception_swallowed(self) -> None:
        """If load_setup_state raises, default prefs are kept."""
        from elle.cli.tui.screens.setup import SetupScreen

        with (
            patch(
                "elle.cli.setup.wizard.load_setup_state",
                side_effect=RuntimeError("no file"),
            ),
            patch(
                "textual.screen.ModalScreen.__init__",
                return_value=None,
            ),
        ):
            screen = SetupScreen(reconfigure=True)

        assert screen.prefs.safety_level == SafetyLevel.STANDARD

    def test_reconfigure_with_none_preferences(self) -> None:
        """If state.preferences is falsy, defaults are kept."""
        saved_state = SetupState(completed=False)
        object.__setattr__(saved_state, "preferences", None)

        from elle.cli.tui.screens.setup import SetupScreen

        with (
            patch(
                "elle.cli.setup.wizard.load_setup_state",
                return_value=saved_state,
            ),
            patch(
                "textual.screen.ModalScreen.__init__",
                return_value=None,
            ),
        ):
            screen = SetupScreen(reconfigure=True)

        assert screen.prefs.safety_level == SafetyLevel.STANDARD

    def test_non_reconfigure_skips_load(self) -> None:
        """reconfigure=False never attempts to load saved state."""
        from elle.cli.tui.screens.setup import SetupScreen

        with patch(
            "textual.screen.ModalScreen.__init__",
            return_value=None,
        ):
            screen = SetupScreen(reconfigure=False)

        assert screen._reconfigure is False
        assert screen.prefs == SetupPreferences()


# ====================================================================
# _render_step mechanics
# ====================================================================


class TestRenderStep:
    """Tests for the step rendering dispatcher."""

    def test_step1_back_disabled(self) -> None:
        screen = _make_screen()
        screen.current_step = 1
        screen._render_step()
        assert screen._mock_back.disabled is True

    def test_step2_back_enabled(self) -> None:
        screen = _make_screen()
        screen.current_step = 2
        screen._render_step()
        assert screen._mock_back.disabled is False

    def test_last_step_shows_confirm(self) -> None:
        from elle.cli.tui.screens.setup import _TOTAL_STEPS

        screen = _make_screen()
        screen.current_step = _TOTAL_STEPS
        screen._render_step()
        assert screen._mock_next.label == "Confirm"
        assert screen._mock_next.variant == "success"

    def test_non_last_step_shows_next(self) -> None:
        screen = _make_screen()
        screen.current_step = 3
        screen._render_step()
        assert screen._mock_next.label == "Next"
        assert screen._mock_next.variant == "primary"

    def test_counter_text_updates(self) -> None:
        from elle.cli.tui.screens.setup import _TOTAL_STEPS

        screen = _make_screen()
        screen.current_step = 4
        # Setting current_step triggers the watcher which calls _render_step,
        # so the counter may have been updated already.  Reset the mock and
        # call _render_step explicitly to get a clean assertion.
        screen._mock_counter.reset_mock()
        screen._render_step()
        screen._mock_counter.update.assert_called_once_with(f"Step 4 of {_TOTAL_STEPS}")

    def test_old_content_removed(self) -> None:
        screen = _make_screen()
        screen.current_step = 1
        screen._render_step()
        screen._mock_step_content.remove.assert_called_once()

    def test_new_content_mounted(self) -> None:
        screen = _make_screen()
        screen.current_step = 1
        screen._render_step()
        screen._mock_content_area.mount.assert_called_once()

    def test_unknown_step_falls_back(self) -> None:
        """A step index outside 1-7 falls back to _step_environment."""
        screen = _make_screen()
        screen.current_step = 99
        # Setting current_step triggers the watcher; reset and re-invoke.
        screen._mock_content_area.reset_mock()
        screen._render_step()
        screen._mock_content_area.mount.assert_called_once()

    def test_all_seven_steps_render(self) -> None:
        """Every step 1-7 renders without raising."""
        from elle.cli.tui.screens.setup import _TOTAL_STEPS

        for step in range(1, _TOTAL_STEPS + 1):
            screen = _make_screen()
            screen.current_step = step
            # Reset after the reactive watcher's automatic call
            screen._mock_content_area.reset_mock()
            screen._render_step()
            screen._mock_content_area.mount.assert_called_once()


# ====================================================================
# Step 1 -- Environment
# ====================================================================


class TestStepEnvironment:
    """Tests for _step_environment."""

    def test_returns_string(self) -> None:
        screen = _make_screen()
        assert isinstance(screen._step_environment(), str)

    def test_header(self) -> None:
        screen = _make_screen()
        assert "Environment Check" in screen._step_environment()

    def test_python_version_shown(self) -> None:
        screen = _make_screen()
        v = sys.version_info
        assert f"{v.major}.{v.minor}.{v.micro}" in screen._step_environment()

    def test_ollama_found(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/ollama"):
            screen = _make_screen()
            result = screen._step_environment()
        assert "found" in result
        assert Icons.SUCCESS in result

    def test_ollama_not_found(self) -> None:
        def _which(cmd):
            return None if cmd == "ollama" else "/usr/bin/" + cmd

        with patch("shutil.which", side_effect=_which):
            screen = _make_screen()
            result = screen._step_environment()
        assert "not found" in result

    def test_ollama_check_exception(self) -> None:
        with patch("shutil.which", side_effect=OSError("broken")):
            screen = _make_screen()
            result = screen._step_environment()
        assert "check failed" in result

    def test_systemd_available(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/systemctl"):
            screen = _make_screen()
            result = screen._step_environment()
        assert "available" in result

    def test_systemd_not_found(self) -> None:
        with patch("shutil.which", return_value=None):
            screen = _make_screen()
            result = screen._step_environment()
        assert "not found" in result

    def test_press_next_hint(self) -> None:
        screen = _make_screen()
        assert "Press Next" in screen._step_environment()

    def test_verifying_message(self) -> None:
        screen = _make_screen()
        assert "Verifying system requirements" in screen._step_environment()


# ====================================================================
# Step 2 -- LLM Provider
# ====================================================================


class TestStepLLMProvider:
    """Tests for _step_llm_provider."""

    def test_returns_string(self) -> None:
        screen = _make_screen()
        assert isinstance(screen._step_llm_provider(), str)

    def test_header(self) -> None:
        screen = _make_screen()
        assert "LLM Provider" in screen._step_llm_provider()

    def test_local_selected_chevron(self) -> None:
        screen = _make_screen(llm_provider=LLMProviderChoice.LOCAL)
        result = screen._step_llm_provider()
        assert Icons.CHEVRON in result
        assert "Ollama" in result

    def test_remote_endpoint_details(self) -> None:
        screen = _make_screen(
            llm_provider=LLMProviderChoice.REMOTE,
            llm_remote_host="https://api.example.com",
            llm_remote_model="gpt-4o",
        )
        result = screen._step_llm_provider()
        assert "Remote Endpoint" in result
        assert "https://api.example.com" in result
        assert "gpt-4o" in result
        assert "ELLE_LLM_API_KEY" in result

    def test_remote_no_host(self) -> None:
        screen = _make_screen(
            llm_provider=LLMProviderChoice.REMOTE,
            llm_remote_host="",
        )
        assert "(not set)" in screen._step_llm_provider()

    def test_local_ensure_ollama_hint(self) -> None:
        screen = _make_screen(llm_provider=LLMProviderChoice.LOCAL)
        assert "Ensure Ollama" in screen._step_llm_provider()

    def test_remote_fallback_note(self) -> None:
        screen = _make_screen(llm_provider=LLMProviderChoice.REMOTE)
        assert "fallback" in screen._step_llm_provider()

    def test_all_providers_listed(self) -> None:
        screen = _make_screen()
        result = screen._step_llm_provider()
        for info in LLM_PROVIDER_INFO.values():
            assert info["name"] in result

    def test_remote_empty_model_fallback(self) -> None:
        """Empty llm_remote_model falls back to 'gpt-4o'."""
        screen = _make_screen(
            llm_provider=LLMProviderChoice.REMOTE,
            llm_remote_model="",
        )
        assert "gpt-4o" in screen._step_llm_provider()


# ====================================================================
# Step 3 -- Safety
# ====================================================================


class TestStepSafety:
    """Tests for _step_safety."""

    def test_returns_string(self) -> None:
        screen = _make_screen()
        assert isinstance(screen._step_safety(), str)

    def test_header(self) -> None:
        screen = _make_screen()
        assert "Safety Settings" in screen._step_safety()

    def test_all_safety_levels(self) -> None:
        screen = _make_screen()
        result = screen._step_safety()
        for info in SAFETY_LEVEL_INFO.values():
            assert info["name"] in result

    def test_selected_level_marked(self) -> None:
        screen = _make_screen(safety_level=SafetyLevel.CAUTIOUS)
        assert Icons.CHEVRON in screen._step_safety()

    def test_all_confirmation_prefs(self) -> None:
        screen = _make_screen()
        result = screen._step_safety()
        for info in CONFIRMATION_INFO.values():
            assert info["name"] in result

    def test_preview_yes(self) -> None:
        screen = _make_screen(require_preview_for_configs=True)
        result = screen._step_safety()
        assert "Preview config changes" in result
        assert "yes" in result

    def test_preview_no(self) -> None:
        screen = _make_screen(require_preview_for_configs=False)
        assert "no" in screen._step_safety()

    def test_confirmation_section(self) -> None:
        screen = _make_screen()
        assert "Confirmation Preference" in screen._step_safety()

    def test_descriptions_present(self) -> None:
        screen = _make_screen()
        result = screen._step_safety()
        for info in SAFETY_LEVEL_INFO.values():
            assert info["description"] in result

    def test_each_level_selectable(self) -> None:
        for level in SafetyLevel:
            screen = _make_screen(safety_level=level)
            assert Icons.CHEVRON in screen._step_safety()


# ====================================================================
# Step 4 -- Telemetry
# ====================================================================


class TestStepTelemetry:
    """Tests for _step_telemetry."""

    def test_returns_string(self) -> None:
        screen = _make_screen()
        assert isinstance(screen._step_telemetry(), str)

    def test_header(self) -> None:
        screen = _make_screen()
        assert "Telemetry Sources" in screen._step_telemetry()

    def test_all_sources_listed(self) -> None:
        screen = _make_screen()
        result = screen._step_telemetry()
        for info in TELEMETRY_INFO.values():
            assert info["name"] in result

    def test_enabled_icon(self) -> None:
        screen = _make_screen(journal_enabled=True)
        assert Icons.SUCCESS in screen._step_telemetry()

    def test_disabled_icon(self) -> None:
        screen = _make_screen(
            journal_enabled=False,
            kernel_enabled=False,
            probes_enabled=False,
            ebpf_enabled=False,
            docker_enabled=False,
        )
        assert Icons.PENDING in screen._step_telemetry()

    def test_ebpf_name_shown(self) -> None:
        screen = _make_screen()
        assert "eBPF Tracing" in screen._step_telemetry()

    def test_descriptions(self) -> None:
        screen = _make_screen()
        result = screen._step_telemetry()
        for info in TELEMETRY_INFO.values():
            assert info["description"] in result

    def test_intro_text(self) -> None:
        screen = _make_screen()
        assert "Select which system events" in screen._step_telemetry()


# ====================================================================
# Step 5 -- Features
# ====================================================================


class TestStepFeatures:
    """Tests for _step_features."""

    def test_returns_string(self) -> None:
        screen = _make_screen()
        assert isinstance(screen._step_features(), str)

    def test_header(self) -> None:
        screen = _make_screen()
        assert "Optional Features" in screen._step_features()

    def test_all_features_listed(self) -> None:
        screen = _make_screen()
        result = screen._step_features()
        for info in FEATURE_INFO.values():
            assert info["name"] in result

    def test_enabled_icon(self) -> None:
        screen = _make_screen(api_enabled=True)
        assert Icons.SUCCESS in screen._step_features()

    def test_disabled_icon(self) -> None:
        screen = _make_screen(api_enabled=False, auto_learn_packages=False)
        assert Icons.PENDING in screen._step_features()

    def test_descriptions(self) -> None:
        screen = _make_screen()
        result = screen._step_features()
        for info in FEATURE_INFO.values():
            assert info["description"] in result


# ====================================================================
# Step 6 -- Privileges
# ====================================================================


class TestStepPrivileges:
    """Tests for _step_privileges."""

    def test_returns_string(self) -> None:
        screen = _make_screen()
        assert isinstance(screen._step_privileges(), str)

    def test_header(self) -> None:
        screen = _make_screen()
        assert "Privilege Configuration" in screen._step_privileges()

    def test_all_levels_listed(self) -> None:
        screen = _make_screen()
        result = screen._step_privileges()
        for info in PRIVILEGE_LEVEL_INFO.values():
            assert info["name"] in result

    def test_selected_level(self) -> None:
        screen = _make_screen(privilege_level=PrivilegeLevel.CONVENIENT)
        assert Icons.CHEVRON in screen._step_privileges()

    def test_passwordless_warning(self) -> None:
        screen = _make_screen(privilege_level=PrivilegeLevel.PASSWORDLESS)
        result = screen._step_privileges()
        assert Icons.WARNING in result
        assert "warning" in PRIVILEGE_LEVEL_INFO[PrivilegeLevel.PASSWORDLESS]

    def test_secure_is_default(self) -> None:
        screen = _make_screen()
        assert screen.prefs.privilege_level == PrivilegeLevel.SECURE

    def test_each_level_selectable(self) -> None:
        for level in PrivilegeLevel:
            screen = _make_screen(privilege_level=level)
            assert Icons.CHEVRON in screen._step_privileges()

    def test_intro_text(self) -> None:
        screen = _make_screen()
        assert "privileged operations" in screen._step_privileges()


# ====================================================================
# Step 7 -- Review
# ====================================================================


class TestStepReview:
    """Tests for _step_review."""

    def test_returns_string(self) -> None:
        screen = _make_screen()
        assert isinstance(screen._step_review(), str)

    def test_header(self) -> None:
        screen = _make_screen()
        assert "Review Configuration" in screen._step_review()

    def test_local_llm_label(self) -> None:
        screen = _make_screen(llm_provider=LLMProviderChoice.LOCAL)
        assert "Local Ollama" in screen._step_review()

    def test_remote_llm_label(self) -> None:
        screen = _make_screen(llm_provider=LLMProviderChoice.REMOTE)
        assert "Remote OpenAI-compatible" in screen._step_review()

    def test_safety_level(self) -> None:
        screen = _make_screen(safety_level=SafetyLevel.CAUTIOUS)
        assert "cautious" in screen._step_review()

    def test_confirmation(self) -> None:
        screen = _make_screen(
            confirmation_preference=ConfirmationPreference.ALWAYS,
        )
        assert "always" in screen._step_review()

    def test_preview_yes(self) -> None:
        screen = _make_screen(require_preview_for_configs=True)
        assert "yes" in screen._step_review()

    def test_preview_no(self) -> None:
        screen = _make_screen(require_preview_for_configs=False)
        assert "no" in screen._step_review()

    def test_privilege_level(self) -> None:
        screen = _make_screen(privilege_level=PrivilegeLevel.CONVENIENT)
        assert "convenient" in screen._step_review()

    def test_telemetry_states(self) -> None:
        screen = _make_screen(
            journal_enabled=True,
            kernel_enabled=False,
            probes_enabled=True,
            ebpf_enabled=False,
            docker_enabled=True,
        )
        result = screen._step_review()
        assert "Journal: on" in result
        assert "Kernel:  off" in result
        assert "Probes:  on" in result
        assert "eBPF:    off" in result
        assert "Docker:  on" in result

    def test_features_states(self) -> None:
        screen = _make_screen(api_enabled=True, auto_learn_packages=False)
        result = screen._step_review()
        assert "REST API:       on" in result
        assert "Auto-learn:     off" in result

    def test_confirm_prompt(self) -> None:
        screen = _make_screen()
        assert "Press Confirm" in screen._step_review()

    def test_telemetry_section_label(self) -> None:
        screen = _make_screen()
        assert "Telemetry:" in screen._step_review()

    def test_features_section_label(self) -> None:
        screen = _make_screen()
        assert "Features:" in screen._step_review()

    def test_all_off(self) -> None:
        screen = _make_screen(
            journal_enabled=False,
            kernel_enabled=False,
            probes_enabled=False,
            ebpf_enabled=False,
            docker_enabled=False,
            api_enabled=False,
            auto_learn_packages=False,
            require_preview_for_configs=False,
        )
        result = screen._step_review()
        assert "Journal: off" in result
        assert "REST API:       off" in result
        assert "Auto-learn:     off" in result

    def test_all_on(self) -> None:
        screen = _make_screen(
            journal_enabled=True,
            kernel_enabled=True,
            probes_enabled=True,
            ebpf_enabled=True,
            docker_enabled=True,
            api_enabled=True,
            auto_learn_packages=True,
            require_preview_for_configs=True,
        )
        result = screen._step_review()
        assert "Journal: on" in result
        assert "REST API:       on" in result
        assert "Auto-learn:     on" in result
        assert "yes" in result


# ====================================================================
# Navigation (on_button_pressed)
# ====================================================================


class TestNavigation:
    """Tests for button press navigation."""

    @staticmethod
    def _btn(button_id: str) -> MagicMock:
        event = MagicMock()
        event.button = MagicMock()
        event.button.id = button_id
        return event

    def test_next_increments(self) -> None:
        screen = _make_screen()
        screen.current_step = 1
        screen.on_button_pressed(self._btn("next"))
        assert screen.current_step == 2

    def test_next_at_last_saves(self) -> None:
        from elle.cli.tui.screens.setup import _TOTAL_STEPS

        screen = _make_screen()
        screen.current_step = _TOTAL_STEPS
        screen._save_and_finish = MagicMock()
        screen.on_button_pressed(self._btn("next"))
        screen._save_and_finish.assert_called_once()

    def test_back_decrements(self) -> None:
        screen = _make_screen()
        screen.current_step = 3
        screen.on_button_pressed(self._btn("back"))
        assert screen.current_step == 2

    def test_back_at_first_stays(self) -> None:
        screen = _make_screen()
        screen.current_step = 1
        screen.on_button_pressed(self._btn("back"))
        assert screen.current_step == 1

    def test_cancel_calls_action_cancel(self) -> None:
        screen = _make_screen()
        screen.action_cancel = MagicMock()
        screen.on_button_pressed(self._btn("cancel"))
        screen.action_cancel.assert_called_once()

    def test_next_from_mid_step(self) -> None:
        screen = _make_screen()
        screen.current_step = 5
        screen.on_button_pressed(self._btn("next"))
        assert screen.current_step == 6


# ====================================================================
# action_cancel
# ====================================================================


class TestActionCancel:
    """Tests for action_cancel."""

    def test_dismiss_false(self) -> None:
        screen = _make_screen()
        screen.dismiss = MagicMock()
        screen.action_cancel()
        screen.dismiss.assert_called_once_with(False)


# ====================================================================
# _save_and_finish
# ====================================================================


class TestSaveAndFinish:
    """Tests for _save_and_finish."""

    def test_saves_state_and_generates_policy(self) -> None:
        screen = _make_screen()
        screen.dismiss = MagicMock()

        mock_save = MagicMock()
        mock_gen = MagicMock()

        with (
            patch("elle.cli.setup.wizard.save_setup_state", mock_save),
            patch("elle.cli.setup.wizard.generate_policy_file", mock_gen),
        ):
            screen._save_and_finish()

        mock_save.assert_called_once()
        mock_gen.assert_called_once()

        saved_state = mock_save.call_args[0][0]
        assert isinstance(saved_state, SetupState)
        assert saved_state.completed is True
        assert saved_state.completed_at is not None
        assert saved_state.preferences is screen.prefs

        assert mock_gen.call_args[0][0] is screen.prefs
        screen.dismiss.assert_called_once_with(True)

    def test_save_exception_still_dismisses(self) -> None:
        screen = _make_screen()
        screen.dismiss = MagicMock()

        with patch(
            "elle.cli.setup.wizard.save_setup_state",
            side_effect=RuntimeError("disk full"),
        ):
            screen._save_and_finish()

        screen.dismiss.assert_called_once_with(True)

    def test_generate_exception_still_dismisses(self) -> None:
        screen = _make_screen()
        screen.dismiss = MagicMock()

        with (
            patch("elle.cli.setup.wizard.save_setup_state"),
            patch(
                "elle.cli.setup.wizard.generate_policy_file",
                side_effect=OSError("perm denied"),
            ),
        ):
            screen._save_and_finish()

        screen.dismiss.assert_called_once_with(True)


# ====================================================================
# watch_current_step / on_mount
# ====================================================================


class TestWatchCurrentStep:
    """Tests for the reactive watcher."""

    def test_calls_render_step(self) -> None:
        screen = _make_screen()
        screen._render_step = MagicMock()
        screen.watch_current_step(2)
        screen._render_step.assert_called_once()


class TestOnMount:
    """Tests for on_mount lifecycle hook."""

    def test_calls_render_step(self) -> None:
        screen = _make_screen()
        screen._render_step = MagicMock()
        screen.on_mount()
        screen._render_step.assert_called_once()


# ====================================================================
# compose
# ====================================================================


class TestCompose:
    """Tests for compose method."""

    def test_compose_is_callable(self) -> None:
        screen = _make_screen()
        assert callable(screen.compose)


# ====================================================================
# Module constants and class metadata
# ====================================================================


class TestModuleConstants:
    """Tests for module-level constants and class metadata."""

    def test_total_steps_is_seven(self) -> None:
        from elle.cli.tui.screens.setup import _TOTAL_STEPS

        assert _TOTAL_STEPS == 7

    def test_escape_binding(self) -> None:
        from elle.cli.tui.screens.setup import SetupScreen

        keys = [b.key for b in SetupScreen.BINDINGS]
        assert "escape" in keys

    def test_css_references_class(self) -> None:
        from elle.cli.tui.screens.setup import SetupScreen

        assert "SetupScreen" in SetupScreen.DEFAULT_CSS

    def test_css_has_wizard_header(self) -> None:
        from elle.cli.tui.screens.setup import SetupScreen

        assert "wizard-header" in SetupScreen.DEFAULT_CSS

    def test_css_has_wizard_nav(self) -> None:
        from elle.cli.tui.screens.setup import SetupScreen

        assert "wizard-nav" in SetupScreen.DEFAULT_CSS


# ====================================================================
# Full navigation scenarios
# ====================================================================


class TestNavigationScenario:
    """Integration-style walk-through tests."""

    def test_walk_forward_all_steps(self) -> None:
        from elle.cli.tui.screens.setup import _TOTAL_STEPS

        screen = _make_screen()
        screen.dismiss = MagicMock()
        screen._save_and_finish = MagicMock()

        for expected in range(2, _TOTAL_STEPS + 1):
            event = MagicMock()
            event.button = MagicMock()
            event.button.id = "next"
            screen.on_button_pressed(event)
            assert screen.current_step == expected

        event = MagicMock()
        event.button = MagicMock()
        event.button.id = "next"
        screen.on_button_pressed(event)
        screen._save_and_finish.assert_called_once()

    def test_walk_back_to_start(self) -> None:
        screen = _make_screen()
        screen.current_step = 5

        for expected in [4, 3, 2, 1]:
            event = MagicMock()
            event.button = MagicMock()
            event.button.id = "back"
            screen.on_button_pressed(event)
            assert screen.current_step == expected

        # Cannot go below 1
        event = MagicMock()
        event.button = MagicMock()
        event.button.id = "back"
        screen.on_button_pressed(event)
        assert screen.current_step == 1

    def test_forward_then_back(self) -> None:
        screen = _make_screen()
        screen.current_step = 1

        for _ in range(2):
            e = MagicMock()
            e.button = MagicMock()
            e.button.id = "next"
            screen.on_button_pressed(e)
        assert screen.current_step == 3

        e = MagicMock()
        e.button = MagicMock()
        e.button.id = "back"
        screen.on_button_pressed(e)
        assert screen.current_step == 2
