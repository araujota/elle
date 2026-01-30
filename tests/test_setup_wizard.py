from __future__ import annotations

"""Tests for the ELLE setup wizard."""

import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from elle.cli.setup.models import (
    ConfirmationPreference,
    PrivilegeLevel,
    SafetyLevel,
    SetupPreferences,
    SetupState,
)
from elle.cli.setup.wizard import (
    SetupWizard,
    StepResult,
    _build_group_rules,
    _build_policy_yaml,
    _build_user_rules,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_prompt():
    """Create a mock EllePrompt instance."""
    prompt = MagicMock()
    prompt.prompt_confirm.return_value = True
    prompt.prompt_select.return_value = "standard"
    prompt.prompt_multi_select.return_value = ["journal", "kernel", "probes", "docker"]
    prompt.prompt_confirm_with_back.return_value = True
    return prompt


@pytest.fixture
def mock_console():
    """Patch the console used by wizard module."""
    with patch("elle.cli.setup.wizard.console") as mock_con:
        yield mock_con


@pytest.fixture
def mock_ui_functions():
    """Patch UI helper functions used by wizard."""
    with (
        patch("elle.cli.setup.wizard.section_rule"),
        patch("elle.cli.setup.wizard.section_header"),
        patch("elle.cli.setup.wizard.print_muted"),
        patch("elle.cli.setup.wizard.print_success"),
        patch("elle.cli.setup.wizard.print_warning"),
        patch("elle.cli.setup.wizard.bulleted_list"),
        patch("elle.cli.setup.wizard.numbered_list"),
        patch("elle.cli.setup.wizard.tip"),
    ):
        yield


@pytest.fixture
def wizard(mock_prompt, mock_console, mock_ui_functions):
    """Create a SetupWizard with mocked dependencies."""
    with patch("elle.cli.setup.wizard.load_setup_state") as mock_load:
        mock_load.return_value = SetupState()
        w = SetupWizard(mock_prompt)
    return w


@pytest.fixture
def tmp_config(tmp_path):
    """Create temporary config directory and files."""
    config_dir = tmp_path / ".config" / "elle"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "elle.toml"
    policy_file = config_dir / "policy.yaml"
    return config_dir, config_file, policy_file


# =============================================================================
# StepResult enum tests
# =============================================================================


class TestStepResult:
    def test_continue_value(self):
        assert StepResult.CONTINUE == "continue"

    def test_back_value(self):
        assert StepResult.BACK == "back"

    def test_cancel_value(self):
        assert StepResult.CANCEL == "cancel"


# =============================================================================
# is_first_run tests
# =============================================================================


class TestIsFirstRun:
    def test_no_config_file(self, tmp_path):
        with patch("elle.cli.setup.wizard.USER_CONFIG_FILE", tmp_path / "nope.toml"):
            from elle.cli.setup.wizard import is_first_run

            assert is_first_run() is True

    def test_config_file_with_completed(self, tmp_config):
        _, config_file, _ = tmp_config
        config_file.write_text("[setup]\ncompleted = true\n")
        with patch("elle.cli.setup.wizard.USER_CONFIG_FILE", config_file):
            from elle.cli.setup.wizard import is_first_run

            assert is_first_run() is False

    def test_config_file_not_completed(self, tmp_config):
        _, config_file, _ = tmp_config
        config_file.write_text("[setup]\ncompleted = false\n")
        with patch("elle.cli.setup.wizard.USER_CONFIG_FILE", config_file):
            from elle.cli.setup.wizard import is_first_run

            assert is_first_run() is True

    def test_config_file_no_setup_section(self, tmp_config):
        _, config_file, _ = tmp_config
        config_file.write_text("[daemon]\n")
        with patch("elle.cli.setup.wizard.USER_CONFIG_FILE", config_file):
            from elle.cli.setup.wizard import is_first_run

            assert is_first_run() is True

    def test_corrupt_config_returns_true(self, tmp_config):
        _, config_file, _ = tmp_config
        config_file.write_text("not valid toml ][}{")
        with patch("elle.cli.setup.wizard.USER_CONFIG_FILE", config_file):
            from elle.cli.setup.wizard import is_first_run

            assert is_first_run() is True


# =============================================================================
# load_setup_state tests
# =============================================================================


class TestLoadSetupState:
    def test_no_config_file(self, tmp_path):
        with patch("elle.cli.setup.wizard.USER_CONFIG_FILE", tmp_path / "none.toml"):
            from elle.cli.setup.wizard import load_setup_state

            state = load_setup_state()
            assert state.completed is False
            assert state.preferences.safety_level == SafetyLevel.STANDARD

    def test_loads_existing_state(self, tmp_config):
        _, config_file, _ = tmp_config
        config_file.write_text(
            '[setup]\ncompleted = true\nversion = "1.0.0"\n\n'
            '[setup.preferences]\nsafety_level = "cautious"\n'
        )
        with patch("elle.cli.setup.wizard.USER_CONFIG_FILE", config_file):
            from elle.cli.setup.wizard import load_setup_state

            state = load_setup_state()
            assert state.completed is True
            assert state.version == "1.0.0"
            assert state.preferences.safety_level == SafetyLevel.CAUTIOUS

    def test_corrupt_config_returns_default(self, tmp_config):
        _, config_file, _ = tmp_config
        config_file.write_text("bad toml }{")
        with patch("elle.cli.setup.wizard.USER_CONFIG_FILE", config_file):
            from elle.cli.setup.wizard import load_setup_state

            state = load_setup_state()
            assert state.completed is False


# =============================================================================
# save_setup_state tests
# =============================================================================


class TestSaveSetupState:
    def test_saves_new_config(self, tmp_config):
        config_dir, config_file, _ = tmp_config
        state = SetupState(
            completed=True,
            completed_at=datetime(2024, 1, 1, 12, 0, 0),
            preferences=SetupPreferences(safety_level=SafetyLevel.CAUTIOUS),
        )

        mock_toml = MagicMock()
        saved_data = {}

        def mock_dump(data, f):
            saved_data.update(data)

        mock_toml.dump = mock_dump
        mock_toml.load.return_value = {}

        with (
            patch("elle.cli.setup.wizard.USER_CONFIG_DIR", config_dir),
            patch("elle.cli.setup.wizard.USER_CONFIG_FILE", config_file),
            patch.dict("sys.modules", {"toml": mock_toml}),
        ):
            from elle.cli.setup.wizard import save_setup_state

            save_setup_state(state)

        assert saved_data["setup"]["completed"] is True
        assert saved_data["setup"]["preferences"]["safety_level"] == "cautious"
        assert saved_data["daemon"]["journal_enabled"] is True

    def test_updates_daemon_section(self, tmp_config):
        config_dir, config_file, _ = tmp_config
        prefs = SetupPreferences(
            journal_enabled=False,
            kernel_enabled=False,
            ebpf_enabled=True,
            api_enabled=False,
            auto_learn_packages=False,
        )
        state = SetupState(preferences=prefs)

        mock_toml = MagicMock()
        saved_data = {}

        def mock_dump(data, f):
            saved_data.update(data)

        mock_toml.dump = mock_dump
        mock_toml.load.return_value = {}

        with (
            patch("elle.cli.setup.wizard.USER_CONFIG_DIR", config_dir),
            patch("elle.cli.setup.wizard.USER_CONFIG_FILE", config_file),
            patch.dict("sys.modules", {"toml": mock_toml}),
        ):
            from elle.cli.setup.wizard import save_setup_state

            save_setup_state(state)

        assert saved_data["daemon"]["journal_enabled"] is False
        assert saved_data["daemon"]["kernel_enabled"] is False
        assert saved_data["daemon"]["ebpf_enabled"] is True


# =============================================================================
# generate_policy_file tests
# =============================================================================


class TestGeneratePolicyFile:
    def test_generates_policy(self, tmp_config):
        config_dir, _, policy_file = tmp_config
        prefs = SetupPreferences()

        with (
            patch("elle.cli.setup.wizard.USER_CONFIG_DIR", config_dir),
            patch("elle.cli.setup.wizard.USER_POLICY_FILE", policy_file),
        ):
            from elle.cli.setup.wizard import generate_policy_file

            generate_policy_file(prefs)

        content = policy_file.read_text()
        assert "version: '1.0'" in content
        assert "name: User Policy" in content

    def test_cautious_safety_rules(self, tmp_config):
        config_dir, _, policy_file = tmp_config
        prefs = SetupPreferences(safety_level=SafetyLevel.CAUTIOUS)

        with (
            patch("elle.cli.setup.wizard.USER_CONFIG_DIR", config_dir),
            patch("elle.cli.setup.wizard.USER_POLICY_FILE", policy_file),
        ):
            from elle.cli.setup.wizard import generate_policy_file

            generate_policy_file(prefs)

        content = policy_file.read_text()
        assert "default_effect: require_confirmation" in content
        assert "cautious-service-restart" in content
        assert "cautious-package-install" in content

    def test_preview_for_configs(self, tmp_config):
        config_dir, _, policy_file = tmp_config
        prefs = SetupPreferences(require_preview_for_configs=True)

        with (
            patch("elle.cli.setup.wizard.USER_CONFIG_DIR", config_dir),
            patch("elle.cli.setup.wizard.USER_POLICY_FILE", policy_file),
        ):
            from elle.cli.setup.wizard import generate_policy_file

            generate_policy_file(prefs)

        content = policy_file.read_text()
        assert "preview-config-changes" in content


# =============================================================================
# _build_policy_yaml tests
# =============================================================================


class TestBuildPolicyYaml:
    def test_standard_safety(self):
        prefs = SetupPreferences(safety_level=SafetyLevel.STANDARD)
        yaml = _build_policy_yaml(prefs)
        assert "default_effect: allow" in yaml

    def test_cautious_safety(self):
        prefs = SetupPreferences(safety_level=SafetyLevel.CAUTIOUS)
        yaml = _build_policy_yaml(prefs)
        assert "default_effect: require_confirmation" in yaml

    def test_minimal_safety(self):
        prefs = SetupPreferences(safety_level=SafetyLevel.MINIMAL)
        yaml = _build_policy_yaml(prefs)
        assert "default_effect: allow" in yaml

    def test_always_confirm(self):
        prefs = SetupPreferences(confirmation_preference=ConfirmationPreference.ALWAYS)
        yaml = _build_policy_yaml(prefs)
        assert "confirm-all-tasks" in yaml

    def test_never_confirm(self):
        prefs = SetupPreferences(confirmation_preference=ConfirmationPreference.NEVER)
        yaml = _build_policy_yaml(prefs)
        assert "skip-confirmation" in yaml

    def test_high_risk_confirm_no_extra_rules(self):
        prefs = SetupPreferences(confirmation_preference=ConfirmationPreference.HIGH_RISK)
        yaml = _build_policy_yaml(prefs)
        assert "confirm-all-tasks" not in yaml
        assert "skip-confirmation" not in yaml

    def test_no_preview_for_configs(self):
        prefs = SetupPreferences(require_preview_for_configs=False)
        yaml = _build_policy_yaml(prefs)
        assert "preview-config-changes" not in yaml

    def test_preview_for_configs_included(self):
        prefs = SetupPreferences(require_preview_for_configs=True)
        yaml = _build_policy_yaml(prefs)
        assert "preview-config-changes" in yaml


# =============================================================================
# _build_group_rules / _build_user_rules tests
# =============================================================================


class TestBuildPolkitRules:
    def test_group_rules_content(self):
        rules = _build_group_rules()
        assert "subject.isInGroup" in rules
        assert "com.elle." in rules
        assert "polkit.Result.YES" in rules

    def test_user_rules_content(self):
        rules = _build_user_rules("testuser")
        assert 'subject.user === "testuser"' in rules
        assert "com.elle." in rules
        assert "polkit.Result.YES" in rules

    def test_user_rules_different_users(self):
        rules1 = _build_user_rules("alice")
        rules2 = _build_user_rules("bob")
        assert "alice" in rules1
        assert "bob" in rules2
        assert "alice" not in rules2


# =============================================================================
# configure_polkit_privileges tests
# =============================================================================


class TestConfigurePolkitPrivileges:
    def test_secure_mode_noop(self):
        from elle.cli.setup.wizard import configure_polkit_privileges

        prefs = SetupPreferences(privilege_level=PrivilegeLevel.SECURE)
        success, msg = configure_polkit_privileges(prefs)
        assert success is True
        assert "secure" in msg.lower()

    def test_convenient_mode(self):
        prefs = SetupPreferences(privilege_level=PrivilegeLevel.CONVENIENT)

        mock_install_result = MagicMock(success=True, authorized=True, error=None)
        mock_group_result = MagicMock(success=True, output="")
        mock_usermod_result = MagicMock(success=True)

        mock_polkit = MagicMock()
        mock_polkit.install_polkit_rules_sync.return_value = mock_install_result
        mock_polkit.create_group_sync.return_value = mock_group_result
        mock_polkit.add_user_to_group_sync.return_value = mock_usermod_result

        with (
            patch("getpass.getuser", return_value="testuser"),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
        ):
            from elle.cli.setup.wizard import configure_polkit_privileges

            success, msg = configure_polkit_privileges(prefs)
            assert success is True
            assert "testuser" in msg

    def test_passwordless_mode(self):
        prefs = SetupPreferences(privilege_level=PrivilegeLevel.PASSWORDLESS)

        mock_install_result = MagicMock(success=True, authorized=True, error=None)

        mock_polkit = MagicMock()
        mock_polkit.install_polkit_rules_sync.return_value = mock_install_result

        with (
            patch("getpass.getuser", return_value="testuser"),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
        ):
            from elle.cli.setup.wizard import configure_polkit_privileges

            success, msg = configure_polkit_privileges(prefs)
            assert success is True
            assert "passwordless" in msg.lower()

    def test_install_fails_returns_error(self):
        prefs = SetupPreferences(privilege_level=PrivilegeLevel.PASSWORDLESS)

        mock_install_result = MagicMock(success=False, authorized=True, error="install failed")

        mock_polkit = MagicMock()
        mock_polkit.install_polkit_rules_sync.return_value = mock_install_result

        with (
            patch("getpass.getuser", return_value="testuser"),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
        ):
            from elle.cli.setup.wizard import configure_polkit_privileges

            success, msg = configure_polkit_privileges(prefs)
            assert success is False
            assert "install failed" in msg


# =============================================================================
# SetupWizard step tests
# =============================================================================


class TestCheckEnvironment:
    def test_returns_continue_when_ollama_running(self, wizard, mock_console):
        wizard._check_ollama = MagicMock(
            return_value={"installed": True, "running": True, "models": ["qwen2.5:7b"]}
        )
        wizard._check_telemetryd_running = MagicMock(return_value=True)
        wizard._check_daemon_running = MagicMock(return_value=True)

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CONTINUE

    def test_ollama_not_installed_continues(self, wizard, mock_console):
        wizard._check_ollama = MagicMock(
            return_value={"installed": False, "running": False, "models": []}
        )
        wizard._check_telemetryd_running = MagicMock(return_value=False)
        wizard._check_daemon_running = MagicMock(return_value=False)
        wizard.prompt.prompt_confirm.return_value = True

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CONTINUE

    def test_cancel_without_ollama(self, wizard, mock_console):
        wizard._check_ollama = MagicMock(
            return_value={"installed": False, "running": False, "models": []}
        )
        wizard._check_telemetryd_running = MagicMock(return_value=False)
        wizard._check_daemon_running = MagicMock(return_value=False)
        wizard.prompt.prompt_confirm.return_value = False

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CANCEL

    def test_ollama_installed_not_running_start_success(self, wizard, mock_console):
        wizard._check_ollama = MagicMock(
            return_value={"installed": True, "running": False, "models": []}
        )
        wizard._check_telemetryd_running = MagicMock(return_value=True)
        wizard._check_daemon_running = MagicMock(return_value=True)
        wizard._start_ollama = MagicMock(return_value=True)
        wizard.prompt.prompt_confirm.return_value = True

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CONTINUE
        assert wizard.prefs.ollama_verified is True


class TestConfigureSafety:
    def test_returns_continue_on_selection(self, wizard, mock_console):
        wizard.prompt.prompt_select.side_effect = ["standard", "high_risk"]
        wizard.prompt.prompt_confirm.return_value = True

        result = wizard._configure_safety(can_go_back=True)
        assert result == StepResult.CONTINUE

    def test_sets_cautious_safety(self, wizard, mock_console):
        wizard.prompt.prompt_select.side_effect = ["cautious", "high_risk"]
        wizard.prompt.prompt_confirm.return_value = True

        wizard._configure_safety(can_go_back=True)
        assert wizard.prefs.safety_level == SafetyLevel.CAUTIOUS

    def test_back_on_none_safety_select(self, wizard, mock_console):
        wizard.prompt.prompt_select.return_value = None

        result = wizard._configure_safety(can_go_back=True)
        assert result == StepResult.BACK

    def test_back_on_none_confirmation_select(self, wizard, mock_console):
        wizard.prompt.prompt_select.side_effect = ["standard", None]

        result = wizard._configure_safety(can_go_back=True)
        assert result == StepResult.BACK

    def test_sets_confirmation_preference(self, wizard, mock_console):
        wizard.prompt.prompt_select.side_effect = ["standard", "always"]
        wizard.prompt.prompt_confirm.return_value = True

        wizard._configure_safety(can_go_back=True)
        assert wizard.prefs.confirmation_preference == ConfirmationPreference.ALWAYS

    def test_sets_never_confirmation(self, wizard, mock_console):
        wizard.prompt.prompt_select.side_effect = ["minimal", "never"]
        wizard.prompt.prompt_confirm.return_value = True

        wizard._configure_safety(can_go_back=True)
        assert wizard.prefs.confirmation_preference == ConfirmationPreference.NEVER

    def test_sets_preview_preference(self, wizard, mock_console):
        wizard.prompt.prompt_select.side_effect = ["standard", "high_risk"]
        wizard.prompt.prompt_confirm.return_value = False

        wizard._configure_safety(can_go_back=True)
        assert wizard.prefs.require_preview_for_configs is False


class TestConfigureTelemetry:
    def test_returns_continue(self, wizard, mock_console):
        wizard.prompt.prompt_multi_select.return_value = ["journal", "kernel"]

        result = wizard._configure_telemetry(can_go_back=True)
        assert result == StepResult.CONTINUE

    def test_sets_telemetry_sources(self, wizard, mock_console):
        wizard.prompt.prompt_multi_select.return_value = ["journal", "ebpf"]

        wizard._configure_telemetry(can_go_back=True)
        assert wizard.prefs.journal_enabled is True
        assert wizard.prefs.kernel_enabled is False
        assert wizard.prefs.ebpf_enabled is True
        assert wizard.prefs.docker_enabled is False

    def test_back_on_none(self, wizard, mock_console):
        wizard.prompt.prompt_multi_select.return_value = None

        result = wizard._configure_telemetry(can_go_back=True)
        assert result == StepResult.BACK

    def test_all_telemetry_selected(self, wizard, mock_console):
        wizard.prompt.prompt_multi_select.return_value = [
            "journal", "kernel", "probes", "docker", "ebpf"
        ]
        wizard._configure_telemetry(can_go_back=True)
        assert wizard.prefs.journal_enabled is True
        assert wizard.prefs.kernel_enabled is True
        assert wizard.prefs.probes_enabled is True
        assert wizard.prefs.docker_enabled is True
        assert wizard.prefs.ebpf_enabled is True

    def test_no_telemetry_selected(self, wizard, mock_console):
        wizard.prompt.prompt_multi_select.return_value = []
        wizard._configure_telemetry(can_go_back=True)
        assert wizard.prefs.journal_enabled is False
        assert wizard.prefs.kernel_enabled is False


class TestConfigureFeatures:
    def test_returns_continue(self, wizard, mock_console):
        wizard.prompt.prompt_multi_select.return_value = ["api"]
        result = wizard._configure_features(can_go_back=True)
        assert result == StepResult.CONTINUE

    def test_sets_features(self, wizard, mock_console):
        wizard.prompt.prompt_multi_select.return_value = ["api", "auto_learn_packages"]
        wizard._configure_features(can_go_back=True)
        assert wizard.prefs.api_enabled is True
        assert wizard.prefs.auto_learn_packages is True

    def test_no_features_selected(self, wizard, mock_console):
        wizard.prompt.prompt_multi_select.return_value = []
        wizard._configure_features(can_go_back=True)
        assert wizard.prefs.api_enabled is False
        assert wizard.prefs.auto_learn_packages is False

    def test_back_on_none(self, wizard, mock_console):
        wizard.prompt.prompt_multi_select.return_value = None
        result = wizard._configure_features(can_go_back=True)
        assert result == StepResult.BACK


class TestConfigurePrivileges:
    def test_secure_mode_continue(self, wizard, mock_console):
        wizard.prompt.prompt_select.return_value = "secure"
        result = wizard._configure_privileges(can_go_back=True)
        assert result == StepResult.CONTINUE
        assert wizard.prefs.privilege_level == PrivilegeLevel.SECURE

    def test_convenient_confirmed(self, wizard, mock_console):
        wizard.prompt.prompt_select.return_value = "convenient"
        wizard.prompt.prompt_confirm.return_value = True
        result = wizard._configure_privileges(can_go_back=True)
        assert result == StepResult.CONTINUE
        assert wizard.prefs.privilege_level == PrivilegeLevel.CONVENIENT

    def test_convenient_denied_reverts(self, wizard, mock_console):
        wizard.prompt.prompt_select.return_value = "convenient"
        wizard.prompt.prompt_confirm.return_value = False
        wizard._configure_privileges(can_go_back=True)
        assert wizard.prefs.privilege_level == PrivilegeLevel.SECURE

    def test_passwordless_confirmed(self, wizard, mock_console):
        wizard.prompt.prompt_select.return_value = "passwordless"
        wizard.prompt.prompt_confirm.return_value = True
        result = wizard._configure_privileges(can_go_back=True)
        assert result == StepResult.CONTINUE
        assert wizard.prefs.privilege_level == PrivilegeLevel.PASSWORDLESS

    def test_passwordless_denied_reverts(self, wizard, mock_console):
        wizard.prompt.prompt_select.return_value = "passwordless"
        wizard.prompt.prompt_confirm.return_value = False
        wizard._configure_privileges(can_go_back=True)
        assert wizard.prefs.privilege_level == PrivilegeLevel.SECURE

    def test_back_on_none(self, wizard, mock_console):
        wizard.prompt.prompt_select.return_value = None
        result = wizard._configure_privileges(can_go_back=True)
        assert result == StepResult.BACK


class TestReviewAndConfirm:
    def test_confirm_returns_continue(self, wizard, mock_console):
        wizard.prompt.prompt_confirm_with_back.return_value = True
        result = wizard._review_and_confirm(can_go_back=True)
        assert result == StepResult.CONTINUE

    def test_deny_returns_cancel(self, wizard, mock_console):
        wizard.prompt.prompt_confirm_with_back.return_value = False
        result = wizard._review_and_confirm(can_go_back=True)
        assert result == StepResult.CANCEL

    def test_back_returns_back(self, wizard, mock_console):
        wizard.prompt.prompt_confirm_with_back.return_value = "back"
        result = wizard._review_and_confirm(can_go_back=True)
        assert result == StepResult.BACK

    def test_review_shows_telemetry_summary(self, wizard, mock_console):
        wizard.prefs.journal_enabled = True
        wizard.prefs.kernel_enabled = True
        wizard.prefs.docker_enabled = False
        wizard.prefs.ebpf_enabled = False
        wizard.prefs.probes_enabled = False
        wizard.prompt.prompt_confirm_with_back.return_value = True

        wizard._review_and_confirm(can_go_back=True)
        # Verifies execution without error (summary panel rendered)
        assert mock_console.print.called


class TestWizardRun:
    def test_successful_run(self, wizard, mock_console):
        wizard._check_environment = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_safety = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_telemetry = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_features = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_privileges = MagicMock(return_value=StepResult.CONTINUE)
        wizard._review_and_confirm = MagicMock(return_value=StepResult.CONTINUE)
        wizard._save_configuration = MagicMock()
        wizard._start_services = MagicMock(return_value=True)
        wizard._show_completion = MagicMock()

        result = wizard.run(reconfigure=False)
        assert result is True

    def test_cancel_returns_false(self, wizard, mock_console):
        wizard._check_environment = MagicMock(return_value=StepResult.CANCEL)

        result = wizard.run(reconfigure=False)
        assert result is False

    def test_keyboard_interrupt_returns_false(self, wizard, mock_console):
        wizard._show_welcome = MagicMock(side_effect=KeyboardInterrupt)

        result = wizard.run(reconfigure=False)
        assert result is False

    def test_reconfigure_shows_different_intro(self, wizard, mock_console):
        wizard._show_reconfigure_intro = MagicMock()
        wizard._show_welcome = MagicMock()
        wizard._check_environment = MagicMock(return_value=StepResult.CANCEL)

        wizard.run(reconfigure=True)
        wizard._show_reconfigure_intro.assert_called_once()
        wizard._show_welcome.assert_not_called()

    def test_back_navigation(self, wizard, mock_console):
        # First call returns BACK, second returns CONTINUE
        call_count = [0]

        def safety_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return StepResult.BACK
            return StepResult.CONTINUE

        wizard._check_environment = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_safety = MagicMock(side_effect=safety_side_effect)
        wizard._configure_telemetry = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_features = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_privileges = MagicMock(return_value=StepResult.CONTINUE)
        wizard._review_and_confirm = MagicMock(return_value=StepResult.CONTINUE)
        wizard._save_configuration = MagicMock()
        wizard._start_services = MagicMock(return_value=True)
        wizard._show_completion = MagicMock()

        result = wizard.run(reconfigure=False)
        assert result is True
        # Safety should have been called at least twice due to going back
        assert wizard._configure_safety.call_count >= 2

    def test_services_failure_still_completes(self, wizard, mock_console):
        wizard._check_environment = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_safety = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_telemetry = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_features = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_privileges = MagicMock(return_value=StepResult.CONTINUE)
        wizard._review_and_confirm = MagicMock(return_value=StepResult.CONTINUE)
        wizard._save_configuration = MagicMock()
        wizard._start_services = MagicMock(return_value=False)
        wizard._show_completion = MagicMock()

        result = wizard.run(reconfigure=False)
        assert result is True


class TestCheckOllama:
    def test_ollama_not_installed(self, wizard):
        with (
            patch("shutil.which", return_value=None),
            patch("elle.cli.setup.wizard.Path") as mock_path_cls,
            patch("subprocess.run", side_effect=FileNotFoundError),
        ):
            mock_path_cls.return_value.exists.return_value = False
            mock_path_cls.home.return_value = Path("/mock/home")
            # Mock httpx to fail (server not running)
            with patch("httpx.get", side_effect=Exception("not running")):
                result = wizard._check_ollama()

        assert result["installed"] is False
        assert result["running"] is False

    def test_ollama_running(self, wizard):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [{"name": "qwen2.5:7b"}, {"name": "nomic-embed-text"}]
        }

        with (
            patch("shutil.which", return_value="/usr/bin/ollama"),
            patch("httpx.get", return_value=mock_response),
        ):
            result = wizard._check_ollama()

        assert result["installed"] is True
        assert result["running"] is True
        assert "qwen2.5:7b" in result["models"]


class TestCheckDaemonRunning:
    def test_daemon_running(self, wizard):
        mock_response = MagicMock()
        mock_response.status_code = 200
        with patch("httpx.get", return_value=mock_response):
            assert wizard._check_daemon_running() is True

    def test_daemon_not_running(self, wizard):
        import httpx

        with patch("httpx.get", side_effect=httpx.RequestError("fail")):
            assert wizard._check_daemon_running() is False


class TestCheckTelemetrydRunning:
    def test_socket_not_exists(self, wizard):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = False
        with patch("pathlib.Path", return_value=mock_path_instance):
            assert wizard._check_telemetryd_running() is False

    def test_socket_connectable(self, wizard):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True

        mock_sock_instance = MagicMock()
        with (
            patch("pathlib.Path", return_value=mock_path_instance),
            patch("socket.socket", return_value=mock_sock_instance),
        ):
            assert wizard._check_telemetryd_running() is True
            mock_sock_instance.connect.assert_called_once()
            mock_sock_instance.close.assert_called_once()

    def test_socket_connection_refused(self, wizard):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True

        mock_sock_instance = MagicMock()
        mock_sock_instance.connect.side_effect = OSError("Connection refused")
        with (
            patch("pathlib.Path", return_value=mock_path_instance),
            patch("socket.socket", return_value=mock_sock_instance),
        ):
            assert wizard._check_telemetryd_running() is False


class TestRunSetupWizard:
    def test_creates_wizard_and_runs(self, mock_console, mock_ui_functions):
        mock_prompt = MagicMock()
        with (
            patch("elle.cli.setup.wizard.load_setup_state", return_value=SetupState()),
            patch.object(SetupWizard, "run", return_value=True),
        ):
            from elle.cli.setup.wizard import run_setup_wizard

            result = run_setup_wizard(mock_prompt, reconfigure=False)
            assert result is True
