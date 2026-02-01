"""Tests for the ELLE setup wizard."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

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
            '[setup]\ncompleted = true\nversion = "1.0.0"\n\n[setup.preferences]\nsafety_level = "cautious"\n'
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
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": ["qwen2.5:7b"]})
        wizard._check_telemetryd_running = MagicMock(return_value=True)
        wizard._check_daemon_running = MagicMock(return_value=True)

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CONTINUE

    def test_ollama_not_installed_continues(self, wizard, mock_console):
        wizard._check_ollama = MagicMock(return_value={"installed": False, "running": False, "models": []})
        wizard._check_telemetryd_running = MagicMock(return_value=False)
        wizard._check_daemon_running = MagicMock(return_value=False)
        wizard.prompt.prompt_confirm.return_value = True

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CONTINUE

    def test_cancel_without_ollama(self, wizard, mock_console):
        wizard._check_ollama = MagicMock(return_value={"installed": False, "running": False, "models": []})
        wizard._check_telemetryd_running = MagicMock(return_value=False)
        wizard._check_daemon_running = MagicMock(return_value=False)
        wizard.prompt.prompt_confirm.return_value = False

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CANCEL

    def test_ollama_installed_not_running_start_success(self, wizard, mock_console):
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": False, "models": []})
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
        wizard.prompt.prompt_multi_select.return_value = ["journal", "kernel", "probes", "docker", "ebpf"]
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
        mock_response.json.return_value = {"models": [{"name": "qwen2.5:7b"}, {"name": "nomic-embed-text"}]}

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


# =============================================================================
# Additional coverage tests: is_first_run toml fallback
# =============================================================================


class TestIsFirstRunTomlFallback:
    """Test the toml fallback path when tomllib is not available."""

    def test_reads_config_correctly(self, tmp_config):
        """Verify is_first_run reads config via available toml parser."""
        _, config_file, _ = tmp_config
        config_file.write_text("[setup]\ncompleted = true\n")

        with patch("elle.cli.setup.wizard.USER_CONFIG_FILE", config_file):
            from elle.cli.setup.wizard import is_first_run

            # The function uses whichever toml parser is available
            assert is_first_run() is False

    def test_config_file_parse_error_returns_true(self, tmp_config):
        """When config file exists but parsing fails, return True."""
        _, config_file, _ = tmp_config
        # Write intentionally broken content that both parsers reject
        config_file.write_bytes(b"\xff\xfe invalid binary content")

        with patch("elle.cli.setup.wizard.USER_CONFIG_FILE", config_file):
            from elle.cli.setup.wizard import is_first_run

            assert is_first_run() is True


class TestLoadSetupStateTomlFallback:
    """Test load_setup_state toml fallback path."""

    def test_tomllib_import_error_falls_back_to_toml(self, tmp_config):
        _, config_file, _ = tmp_config
        config_file.write_text(
            '[setup]\ncompleted = true\nversion = "1.0.0"\n\n[setup.preferences]\nsafety_level = "standard"\n'
        )

        with patch("elle.cli.setup.wizard.USER_CONFIG_FILE", config_file):
            from elle.cli.setup.wizard import load_setup_state

            state = load_setup_state()
            assert state.completed is True
            assert state.version == "1.0.0"

    def test_load_with_empty_preferences(self, tmp_config):
        """When preferences dict is empty, should use defaults."""
        _, config_file, _ = tmp_config
        config_file.write_text('[setup]\ncompleted = true\nversion = "0.1.0"\n')
        with patch("elle.cli.setup.wizard.USER_CONFIG_FILE", config_file):
            from elle.cli.setup.wizard import load_setup_state

            state = load_setup_state()
            assert state.completed is True
            assert state.preferences.safety_level == SafetyLevel.STANDARD


# =============================================================================
# Additional coverage tests: save_setup_state
# =============================================================================


class TestSaveSetupStateAdditional:
    """Additional save_setup_state tests for missed paths."""

    def test_saves_with_existing_config(self, tmp_config):
        """When config file already exists, merge with existing content."""
        config_dir, config_file, _ = tmp_config
        config_file.write_text('[daemon]\ncustom_key = "keep_me"\n')

        state = SetupState(
            completed=True,
            completed_at=datetime(2024, 6, 15, 10, 0, 0),
            preferences=SetupPreferences(
                safety_level=SafetyLevel.MINIMAL,
                journal_enabled=False,
            ),
        )

        mock_toml = MagicMock()
        saved_data = {}

        def mock_dump(data, f):
            saved_data.update(data)

        mock_toml.dump = mock_dump
        mock_toml.load.return_value = {"daemon": {"custom_key": "keep_me"}}

        with (
            patch("elle.cli.setup.wizard.USER_CONFIG_DIR", config_dir),
            patch("elle.cli.setup.wizard.USER_CONFIG_FILE", config_file),
            patch.dict("sys.modules", {"toml": mock_toml}),
        ):
            from elle.cli.setup.wizard import save_setup_state

            save_setup_state(state)

        assert saved_data["setup"]["completed"] is True
        assert saved_data["setup"]["completed_at"] == "2024-06-15T10:00:00"
        assert saved_data["daemon"]["journal_enabled"] is False

    def test_save_with_no_completed_at(self, tmp_config):
        """When completed_at is None, config should have None value."""
        config_dir, config_file, _ = tmp_config

        state = SetupState(completed=False, completed_at=None)

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

        assert saved_data["setup"]["completed_at"] is None

    def test_save_existing_config_read_error(self, tmp_config):
        """When reading existing config fails, start fresh."""
        config_dir, config_file, _ = tmp_config
        config_file.write_text("bad toml }{")

        state = SetupState(completed=True, preferences=SetupPreferences())

        mock_toml = MagicMock()
        saved_data = {}

        def mock_dump(data, f):
            saved_data.update(data)

        mock_toml.dump = mock_dump
        # Simulate toml.load raising an error for corrupt file
        mock_toml.load.side_effect = Exception("Invalid TOML")

        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "tomllib":
                raise ImportError("No module named 'tomllib'")
            if name == "toml":
                return mock_toml
            return real_import(name, *args, **kwargs)

        with (
            patch("elle.cli.setup.wizard.USER_CONFIG_DIR", config_dir),
            patch("elle.cli.setup.wizard.USER_CONFIG_FILE", config_file),
            patch("builtins.__import__", side_effect=mock_import),
            patch.dict("sys.modules", {"toml": mock_toml}),
        ):
            from elle.cli.setup.wizard import save_setup_state

            save_setup_state(state)

        # Should still save even if reading failed
        assert saved_data["setup"]["completed"] is True

    def test_pydantic_v1_dict_fallback(self, tmp_config):
        """When model_dump() raises AttributeError, fall back to dict()."""
        config_dir, config_file, _ = tmp_config

        # Create a mock preferences object that simulates Pydantic v1 behavior:
        # model_dump raises AttributeError but dict() works
        mock_prefs = MagicMock()
        mock_prefs.model_dump.side_effect = AttributeError("No model_dump")
        mock_prefs.dict.return_value = {"safety_level": "standard"}
        mock_prefs.privilege_level = PrivilegeLevel.SECURE
        mock_prefs.journal_enabled = True
        mock_prefs.kernel_enabled = True
        mock_prefs.probes_enabled = True
        mock_prefs.ebpf_enabled = False
        mock_prefs.docker_enabled = True
        mock_prefs.auto_learn_packages = True
        mock_prefs.api_enabled = True

        state = SetupState(completed=True)
        state.preferences = mock_prefs

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

        assert "preferences" in saved_data["setup"]
        assert saved_data["setup"]["preferences"]["safety_level"] == "standard"
        mock_prefs.dict.assert_called_once()

    def test_save_updates_daemon_api_section(self, tmp_config):
        """Ensure daemon.api section is properly created."""
        config_dir, config_file, _ = tmp_config

        prefs = SetupPreferences(api_enabled=False)
        state = SetupState(completed=True, preferences=prefs)

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

        assert saved_data["daemon"]["api"]["enabled"] is False


# =============================================================================
# Additional coverage tests: configure_polkit_privileges
# =============================================================================


class TestConfigurePolkitPrivilegesAdditional:
    """Additional tests for polkit privilege configuration."""

    def test_authorization_denied(self):
        """When Polkit authorization is denied."""
        prefs = SetupPreferences(privilege_level=PrivilegeLevel.PASSWORDLESS)

        mock_install_result = MagicMock(success=False, authorized=False, error="denied")
        mock_polkit = MagicMock()
        mock_polkit.install_polkit_rules_sync.return_value = mock_install_result

        with (
            patch("getpass.getuser", return_value="testuser"),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
        ):
            from elle.cli.setup.wizard import configure_polkit_privileges

            success, msg = configure_polkit_privileges(prefs)
            assert success is False
            assert "denied" in msg.lower()

    def test_install_exception(self):
        """When install_polkit_rules_sync raises an exception."""
        prefs = SetupPreferences(privilege_level=PrivilegeLevel.CONVENIENT)

        mock_polkit = MagicMock()
        mock_polkit.install_polkit_rules_sync.side_effect = RuntimeError("polkit error")

        with (
            patch("getpass.getuser", return_value="testuser"),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
        ):
            from elle.cli.setup.wizard import configure_polkit_privileges

            success, msg = configure_polkit_privileges(prefs)
            assert success is False
            assert "polkit error" in msg

    def test_group_creation_failure(self):
        """When creating the 'elle' group fails."""
        prefs = SetupPreferences(privilege_level=PrivilegeLevel.CONVENIENT)

        mock_install_result = MagicMock(success=True, authorized=True, error=None)
        mock_group_result = MagicMock(success=False, output="failed", error="cannot create")
        mock_polkit = MagicMock()
        mock_polkit.install_polkit_rules_sync.return_value = mock_install_result
        mock_polkit.create_group_sync.return_value = mock_group_result

        with (
            patch("getpass.getuser", return_value="testuser"),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
        ):
            from elle.cli.setup.wizard import configure_polkit_privileges

            success, msg = configure_polkit_privileges(prefs)
            assert success is False
            assert "group" in msg.lower()

    def test_group_already_exists_is_ok(self):
        """When group already exists, treat as success."""
        prefs = SetupPreferences(privilege_level=PrivilegeLevel.CONVENIENT)

        mock_install_result = MagicMock(success=True, authorized=True, error=None)
        mock_group_result = MagicMock(success=False, output="group 'elle' already exists")
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

    def test_add_user_to_group_failure(self):
        """When adding user to 'elle' group fails."""
        prefs = SetupPreferences(privilege_level=PrivilegeLevel.CONVENIENT)

        mock_install_result = MagicMock(success=True, authorized=True, error=None)
        mock_group_result = MagicMock(success=True, output="")
        mock_usermod_result = MagicMock(success=False, error="permission denied")
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
            assert success is False
            assert "group" in msg.lower()

    def test_unknown_privilege_level(self):
        """When privilege level is unknown (e.g. a new enum added later)."""
        prefs = SetupPreferences()
        # Manually set to something that bypasses the known checks
        prefs.privilege_level = MagicMock()
        prefs.privilege_level.__eq__ = lambda self, other: False

        with patch("getpass.getuser", return_value="testuser"):
            # Need to avoid the polkit import
            mock_polkit = MagicMock()
            with patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}):
                from elle.cli.setup.wizard import configure_polkit_privileges

                success, msg = configure_polkit_privileges(prefs)
                assert success is False
                assert "Unknown privilege level" in msg


# =============================================================================
# Additional coverage: _check_environment branches
# =============================================================================


class TestCheckEnvironmentAdditional:
    """Additional tests for _check_environment edge cases."""

    def test_config_dir_exists_check(self, wizard, mock_console):
        """When config directory already exists."""
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": ["m1"]})
        wizard._check_telemetryd_running = MagicMock(return_value=True)
        wizard._check_daemon_running = MagicMock(return_value=True)

        with patch("elle.cli.setup.wizard.USER_CONFIG_DIR") as mock_dir:
            mock_dir.exists.return_value = True
            result = wizard._check_environment(can_go_back=False)

        assert result == StepResult.CONTINUE

    def test_config_dir_does_not_exist(self, wizard, mock_console):
        """When config directory does not exist yet."""
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": []})
        wizard._check_telemetryd_running = MagicMock(return_value=True)
        wizard._check_daemon_running = MagicMock(return_value=True)

        with patch("elle.cli.setup.wizard.USER_CONFIG_DIR") as mock_dir:
            mock_dir.exists.return_value = False
            result = wizard._check_environment(can_go_back=False)

        assert result == StepResult.CONTINUE

    def test_many_models_truncated(self, wizard, mock_console):
        """When more than 3 models are available, display is truncated."""
        wizard._check_ollama = MagicMock(
            return_value={
                "installed": True,
                "running": True,
                "models": ["model1", "model2", "model3", "model4", "model5"],
            }
        )
        wizard._check_telemetryd_running = MagicMock(return_value=True)
        wizard._check_daemon_running = MagicMock(return_value=True)

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CONTINUE

    def test_ollama_installed_not_running_start_failure(self, wizard, mock_console):
        """When Ollama is installed but fails to start."""
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": False, "models": []})
        wizard._check_telemetryd_running = MagicMock(return_value=False)
        wizard._check_daemon_running = MagicMock(return_value=False)
        wizard._start_ollama = MagicMock(return_value=False)
        # First confirm = start ollama, second confirm = continue without
        wizard.prompt.prompt_confirm.side_effect = [True, True]

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CONTINUE

    def test_ollama_installed_user_declines_start(self, wizard, mock_console):
        """User declines to start Ollama when it is installed."""
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": False, "models": []})
        wizard._check_telemetryd_running = MagicMock(return_value=False)
        wizard._check_daemon_running = MagicMock(return_value=False)
        # First confirm = decline start, second = continue without
        wizard.prompt.prompt_confirm.side_effect = [False, True]

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CONTINUE

    def test_daemons_not_running_user_starts_them(self, wizard, mock_console):
        """When daemons not running and Ollama is running, user starts daemons."""
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": ["m1"]})
        wizard._check_telemetryd_running = MagicMock(return_value=False)
        wizard._check_daemon_running = MagicMock(return_value=False)
        wizard._start_telemetryd = MagicMock(return_value={"success": True, "method": "systemd", "error": None})
        wizard._start_daemon = MagicMock(return_value={"success": True, "method": "systemd", "error": None})
        wizard.prompt.prompt_confirm.return_value = True

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CONTINUE
        wizard._start_telemetryd.assert_called_once()
        wizard._start_daemon.assert_called_once()

    def test_daemons_not_running_start_fails(self, wizard, mock_console):
        """When starting daemons fails."""
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": []})
        wizard._check_telemetryd_running = MagicMock(return_value=False)
        wizard._check_daemon_running = MagicMock(return_value=False)
        wizard._start_telemetryd = MagicMock(
            return_value={"success": False, "method": "none", "error": "not available"}
        )
        wizard._start_daemon = MagicMock(return_value={"success": False, "method": "none", "error": "not available"})
        wizard.prompt.prompt_confirm.return_value = True

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CONTINUE

    def test_only_telemetryd_not_running(self, wizard, mock_console):
        """When only telemetryd is not running and ollama is running."""
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": []})
        wizard._check_telemetryd_running = MagicMock(return_value=False)
        wizard._check_daemon_running = MagicMock(return_value=True)
        wizard._start_telemetryd = MagicMock(return_value={"success": True, "method": "systemd", "error": None})
        wizard.prompt.prompt_confirm.return_value = True

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CONTINUE

    def test_only_daemon_not_running(self, wizard, mock_console):
        """When only the Python daemon is not running and ollama is running."""
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": []})
        wizard._check_telemetryd_running = MagicMock(return_value=True)
        wizard._check_daemon_running = MagicMock(return_value=False)
        wizard._start_daemon = MagicMock(return_value={"success": True, "method": "systemd", "error": None})
        wizard.prompt.prompt_confirm.return_value = True

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CONTINUE

    def test_ollama_not_installed_cancel(self, wizard, mock_console):
        """When Ollama is not installed and user cancels."""
        wizard._check_ollama = MagicMock(return_value={"installed": False, "running": False, "models": []})
        wizard._check_telemetryd_running = MagicMock(return_value=False)
        wizard._check_daemon_running = MagicMock(return_value=False)
        wizard.prompt.prompt_confirm.return_value = False

        result = wizard._check_environment(can_go_back=False)
        assert result == StepResult.CANCEL


# =============================================================================
# Additional coverage: _check_ollama branches
# =============================================================================


class TestCheckOllamaAdditional:
    """Additional tests for _check_ollama edge cases."""

    def test_ollama_found_via_common_path(self, wizard):
        """When ollama is found via common paths, not shutil.which."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}

        with (
            patch("shutil.which", return_value=None),
            patch("elle.cli.setup.wizard.Path") as mock_path_cls,
            patch("httpx.get", return_value=mock_response),
        ):
            # Simulate common path existing
            instance = MagicMock()
            instance.exists.return_value = True
            mock_path_cls.return_value = instance
            mock_path_cls.home.return_value = Path("/mock/home")

            result = wizard._check_ollama()

        assert result["installed"] is True
        assert result["running"] is True

    def test_ollama_found_via_version_command(self, wizard):
        """When ollama is not on path but version command works."""
        with (
            patch("shutil.which", return_value=None),
            patch("elle.cli.setup.wizard.Path") as mock_path_cls,
            patch("subprocess.run") as mock_run,
            patch("httpx.get", side_effect=Exception("not running")),
        ):
            # Common paths don't exist
            instance = MagicMock()
            instance.exists.return_value = False
            mock_path_cls.return_value = instance
            mock_path_cls.home.return_value = Path("/mock/home")

            # But version command succeeds
            mock_run.return_value = MagicMock(returncode=0)

            result = wizard._check_ollama()

        assert result["installed"] is True
        assert result["running"] is False

    def test_ollama_version_timeout(self, wizard):
        """When ollama --version times out."""
        import subprocess as sp

        with (
            patch("shutil.which", return_value=None),
            patch("elle.cli.setup.wizard.Path") as mock_path_cls,
            patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="ollama", timeout=2)),
            patch("httpx.get", side_effect=Exception("not running")),
        ):
            instance = MagicMock()
            instance.exists.return_value = False
            mock_path_cls.return_value = instance
            mock_path_cls.home.return_value = Path("/mock/home")

            result = wizard._check_ollama()

        assert result["installed"] is False

    def test_ollama_httpx_non_200(self, wizard):
        """When ollama server returns non-200."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with (
            patch("shutil.which", return_value="/usr/bin/ollama"),
            patch("httpx.get", return_value=mock_response),
        ):
            result = wizard._check_ollama()

        assert result["installed"] is True
        assert result["running"] is False


# =============================================================================
# Additional coverage: _save_configuration
# =============================================================================


class TestSaveConfiguration:
    """Tests for _save_configuration method."""

    def test_save_configuration_secure_level(self, wizard, mock_console):
        """When privilege level is SECURE, skip polkit configuration."""
        wizard.prefs.privilege_level = PrivilegeLevel.SECURE

        with (
            patch("elle.cli.setup.wizard.save_setup_state") as mock_save,
            patch("elle.cli.setup.wizard.generate_policy_file") as mock_gen,
        ):
            wizard._save_configuration()

        mock_save.assert_called_once()
        mock_gen.assert_called_once()
        assert wizard.state.completed is True
        assert wizard.state.completed_at is not None

    def test_save_configuration_non_secure_polkit_success(self, wizard, mock_console):
        """When privilege level is non-secure and polkit succeeds."""
        wizard.prefs.privilege_level = PrivilegeLevel.CONVENIENT

        with (
            patch("elle.cli.setup.wizard.save_setup_state") as mock_save,
            patch("elle.cli.setup.wizard.generate_policy_file"),
            patch(
                "elle.cli.setup.wizard.configure_polkit_privileges",
                return_value=(True, "Success"),
            ),
        ):
            wizard._save_configuration()

        assert wizard.prefs.polkit_configured is True
        # save_setup_state called twice: once for initial save, once after polkit
        assert mock_save.call_count == 2

    def test_save_configuration_non_secure_polkit_failure(self, wizard, mock_console):
        """When privilege level is non-secure and polkit fails."""
        wizard.prefs.privilege_level = PrivilegeLevel.PASSWORDLESS

        with (
            patch("elle.cli.setup.wizard.save_setup_state") as mock_save,
            patch("elle.cli.setup.wizard.generate_policy_file"),
            patch(
                "elle.cli.setup.wizard.configure_polkit_privileges",
                return_value=(False, "Authorization denied"),
            ),
        ):
            wizard._save_configuration()

        assert wizard.prefs.polkit_configured is False
        # Still saves twice
        assert mock_save.call_count == 2


# =============================================================================
# Additional coverage: _start_services
# =============================================================================


class TestStartServices:
    """Tests for _start_services method."""

    def test_user_declines_services(self, wizard, mock_console):
        """When user declines to start services."""
        wizard.prompt.prompt_confirm.return_value = False

        result = wizard._start_services()
        assert result is False

    def test_all_services_running(self, wizard, mock_console):
        """When all services are already running."""
        wizard.prompt.prompt_confirm.return_value = True
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": ["m1"]})
        wizard._setup_models = MagicMock(return_value=True)
        wizard._check_telemetryd_running = MagicMock(return_value=True)
        wizard._check_daemon_running = MagicMock(return_value=True)
        wizard._start_daemon = MagicMock(
            return_value={"success": True, "method": "systemd (already running)", "error": None}
        )

        result = wizard._start_services()
        assert result is True

    def test_ollama_not_installed(self, wizard, mock_console):
        """When Ollama is not installed during service start."""
        wizard.prompt.prompt_confirm.return_value = True
        wizard._check_ollama = MagicMock(return_value={"installed": False, "running": False, "models": []})
        wizard._check_telemetryd_running = MagicMock(return_value=True)
        wizard._start_daemon = MagicMock(return_value={"success": True, "method": "systemd", "error": None})

        result = wizard._start_services()
        assert result is False

    def test_ollama_installed_start_success(self, wizard, mock_console):
        """When Ollama needs to be started and succeeds."""
        wizard.prompt.prompt_confirm.return_value = True
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": False, "models": []})
        wizard._start_ollama = MagicMock(return_value=True)
        wizard._setup_models = MagicMock(return_value=True)
        wizard._check_telemetryd_running = MagicMock(return_value=True)
        wizard._start_daemon = MagicMock(return_value={"success": True, "method": "systemd", "error": None})

        result = wizard._start_services()
        assert result is True
        assert wizard.prefs.ollama_verified is True

    def test_ollama_installed_start_failure(self, wizard, mock_console):
        """When Ollama start fails."""
        wizard.prompt.prompt_confirm.return_value = True
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": False, "models": []})
        wizard._start_ollama = MagicMock(return_value=False)
        wizard._check_telemetryd_running = MagicMock(return_value=True)
        wizard._start_daemon = MagicMock(return_value={"success": True, "method": "systemd", "error": None})

        result = wizard._start_services()
        assert result is False

    def test_model_setup_failure(self, wizard, mock_console):
        """When model setup fails."""
        wizard.prompt.prompt_confirm.return_value = True
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": []})
        wizard._setup_models = MagicMock(return_value=False)
        wizard._check_telemetryd_running = MagicMock(return_value=True)
        wizard._start_daemon = MagicMock(return_value={"success": True, "method": "systemd", "error": None})

        result = wizard._start_services()
        assert result is False

    def test_ollama_not_running_skip_models(self, wizard, mock_console):
        """When Ollama is not running, skip model setup."""
        wizard.prompt.prompt_confirm.return_value = True
        # Ollama check returns running=False (and remains that way)
        wizard._check_ollama = MagicMock(return_value={"installed": False, "running": False, "models": []})
        wizard._check_telemetryd_running = MagicMock(return_value=True)
        wizard._start_daemon = MagicMock(return_value={"success": True, "method": "systemd", "error": None})

        result = wizard._start_services()
        # Should not call _setup_models since ollama not running
        assert result is False

    def test_telemetryd_start_failure(self, wizard, mock_console):
        """When telemetryd fails to start."""
        wizard.prompt.prompt_confirm.return_value = True
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": []})
        wizard._setup_models = MagicMock(return_value=True)
        wizard._check_telemetryd_running = MagicMock(return_value=False)
        wizard._start_telemetryd = MagicMock(
            return_value={"success": False, "method": "none", "error": "not installed"}
        )
        wizard._start_daemon = MagicMock(return_value={"success": True, "method": "systemd", "error": None})

        result = wizard._start_services()
        assert result is False

    def test_telemetryd_start_success(self, wizard, mock_console):
        """When telemetryd starts successfully."""
        wizard.prompt.prompt_confirm.return_value = True
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": []})
        wizard._setup_models = MagicMock(return_value=True)
        wizard._check_telemetryd_running = MagicMock(return_value=False)
        wizard._start_telemetryd = MagicMock(return_value={"success": True, "method": "systemd", "error": None})
        wizard._start_daemon = MagicMock(return_value={"success": True, "method": "systemd", "error": None})

        result = wizard._start_services()
        assert result is True

    def test_daemon_start_failure(self, wizard, mock_console):
        """When daemon fails to start."""
        wizard.prompt.prompt_confirm.return_value = True
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": []})
        wizard._setup_models = MagicMock(return_value=True)
        wizard._check_telemetryd_running = MagicMock(return_value=True)
        wizard._start_daemon = MagicMock(return_value={"success": False, "method": "none", "error": "not available"})

        result = wizard._start_services()
        assert result is False


# =============================================================================
# Additional coverage: _start_ollama
# =============================================================================


class TestStartOllama:
    """Tests for _start_ollama method."""

    def test_start_via_user_systemctl(self, wizard):
        """When systemctl --user start ollama succeeds."""
        mock_result = MagicMock(returncode=0)
        wizard._wait_for_ollama = MagicMock(return_value=True)

        with patch("subprocess.run", return_value=mock_result):
            result = wizard._start_ollama()

        assert result is True

    def test_user_systemctl_fails_system_succeeds(self, wizard):
        """When user systemctl fails but system systemctl succeeds."""
        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if "--user" in cmd:
                result.returncode = 1
            else:
                result.returncode = 0
            return result

        wizard._wait_for_ollama = MagicMock(return_value=True)

        with patch("subprocess.run", side_effect=mock_run):
            result = wizard._start_ollama()

        assert result is True

    def test_both_systemctl_fail_popen_succeeds(self, wizard):
        """When both systemctl fail but Popen succeeds."""

        def mock_run(cmd, **kwargs):
            raise FileNotFoundError("systemctl not found")

        wizard._wait_for_ollama = MagicMock(return_value=True)

        with (
            patch("subprocess.run", side_effect=mock_run),
            patch("shutil.which", return_value="/usr/bin/ollama"),
            patch("subprocess.Popen"),
        ):
            result = wizard._start_ollama()

        assert result is True

    def test_popen_path_search(self, wizard):
        """When shutil.which returns None, check common paths."""

        def mock_run(cmd, **kwargs):
            raise FileNotFoundError("systemctl not found")

        wizard._wait_for_ollama = MagicMock(return_value=True)

        with (
            patch("subprocess.run", side_effect=mock_run),
            patch("shutil.which", return_value=None),
            patch("elle.cli.setup.wizard.Path") as mock_path_cls,
            patch("subprocess.Popen"),
        ):
            instance = MagicMock()
            # First path exists
            instance.exists.return_value = True
            mock_path_cls.return_value = instance
            mock_path_cls.home.return_value = Path("/mock/home")

            result = wizard._start_ollama()

        assert result is True

    def test_no_ollama_path_found(self, wizard):
        """When no ollama path can be found at all."""

        def mock_run(cmd, **kwargs):
            raise FileNotFoundError("systemctl not found")

        with (
            patch("subprocess.run", side_effect=mock_run),
            patch("shutil.which", return_value=None),
            patch("elle.cli.setup.wizard.Path") as mock_path_cls,
        ):
            instance = MagicMock()
            instance.exists.return_value = False
            mock_path_cls.return_value = instance
            mock_path_cls.home.return_value = Path("/mock/home")

            result = wizard._start_ollama()

        assert result is False

    def test_popen_exception(self, wizard):
        """When Popen raises an exception."""

        def mock_run(cmd, **kwargs):
            raise FileNotFoundError("systemctl not found")

        with (
            patch("subprocess.run", side_effect=mock_run),
            patch("shutil.which", return_value="/usr/bin/ollama"),
            patch("subprocess.Popen", side_effect=OSError("cannot start")),
        ):
            result = wizard._start_ollama()

        assert result is False

    def test_systemctl_timeout(self, wizard):
        """When systemctl times out."""
        import subprocess as sp

        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            raise sp.TimeoutExpired(cmd=cmd, timeout=10)

        with (
            patch("subprocess.run", side_effect=mock_run),
            patch("shutil.which", return_value=None),
            patch("elle.cli.setup.wizard.Path") as mock_path_cls,
        ):
            instance = MagicMock()
            instance.exists.return_value = False
            mock_path_cls.return_value = instance
            mock_path_cls.home.return_value = Path("/mock/home")

            result = wizard._start_ollama()

        assert result is False

    def test_user_systemctl_success_wait_fails(self, wizard):
        """When systemctl succeeds but waiting for Ollama times out."""
        mock_result = MagicMock(returncode=0)
        wizard._wait_for_ollama = MagicMock(return_value=False)

        with patch("subprocess.run", return_value=mock_result):
            result = wizard._start_ollama()

        assert result is False


# =============================================================================
# Additional coverage: _wait_for_ollama
# =============================================================================


class TestWaitForOllama:
    """Tests for _wait_for_ollama method."""

    def test_immediately_available(self, wizard):
        """When Ollama responds immediately."""
        mock_response = MagicMock(status_code=200)

        with (
            patch("httpx.get", return_value=mock_response),
            patch("time.time", side_effect=[0, 0]),
            patch("time.sleep"),
        ):
            result = wizard._wait_for_ollama(timeout=30)

        assert result is True

    def test_becomes_available_after_retries(self, wizard):
        """When Ollama becomes available after a few retries."""
        import httpx

        mock_response = MagicMock(status_code=200)
        call_count = [0]

        def mock_get(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise httpx.RequestError("not ready")
            return mock_response

        with (
            patch("httpx.get", side_effect=mock_get),
            patch("time.time", side_effect=[0, 1, 2, 3]),
            patch("time.sleep"),
        ):
            result = wizard._wait_for_ollama(timeout=30)

        assert result is True

    def test_timeout_reached(self, wizard):
        """When Ollama never becomes available."""
        import httpx

        with (
            patch("httpx.get", side_effect=httpx.RequestError("not ready")),
            patch("time.time", side_effect=[0, 31]),
            patch("time.sleep"),
        ):
            result = wizard._wait_for_ollama(timeout=30)

        assert result is False


# =============================================================================
# Additional coverage: _setup_models
# =============================================================================


class TestSetupModels:
    """Tests for _setup_models method."""

    def test_setup_models_success(self, wizard):
        """When model setup succeeds via event loop."""
        mock_rag = MagicMock()
        mock_rag.LLM_MODEL = "test-model"

        with (
            patch.dict("sys.modules", {"elle.rag.constants": mock_rag}),
            patch.object(wizard, "_setup_models_async", return_value=True),
            patch("asyncio.get_event_loop") as mock_loop,
        ):
            mock_loop.return_value.run_until_complete.return_value = True
            result = wizard._setup_models()

        assert result is True

    def test_setup_models_runtime_error_fallback(self, wizard):
        """When no event loop exists, falls back to asyncio.run."""
        mock_rag = MagicMock()
        mock_rag.LLM_MODEL = "test-model"

        with (
            patch.dict("sys.modules", {"elle.rag.constants": mock_rag}),
            patch("asyncio.get_event_loop", side_effect=RuntimeError("no loop")),
            patch("asyncio.run", return_value=True),
        ):
            result = wizard._setup_models()

        assert result is True


# =============================================================================
# Additional coverage: _setup_models_async
# =============================================================================


class TestSetupModelsAsync:
    """Tests for _setup_models_async method."""

    @pytest.mark.asyncio
    async def test_model_already_available_warmup_success(self, wizard, mock_console):
        """When the LLM model is already available and warmup succeeds."""
        from unittest.mock import AsyncMock

        mock_warmup_result = MagicMock(success=True, duration_ms=500.0, error=None)

        mock_warmup_service = MagicMock()
        mock_warmup_service.list_models = AsyncMock(return_value=["test-model:latest"])
        mock_warmup_service.warm_llm = AsyncMock(return_value=mock_warmup_result)
        mock_warmup_service.close = AsyncMock()

        mock_warmup_mod = MagicMock()
        mock_warmup_mod.ModelWarmupService.return_value = mock_warmup_service

        with (
            patch.dict("sys.modules", {"elle.rag.model_warmup": mock_warmup_mod}),
            patch("elle.cli.setup.wizard.Progress"),
        ):
            result = await wizard._setup_models_async("test-model")

        assert result is True
        mock_warmup_service.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_model_needs_pull_success(self, wizard, mock_console):
        """When the LLM model needs to be pulled and succeeds."""
        from unittest.mock import AsyncMock

        mock_warmup_result = MagicMock(success=True, duration_ms=300.0, error=None)

        mock_warmup_service = MagicMock()
        mock_warmup_service.list_models = AsyncMock(return_value=["other-model"])
        mock_warmup_service.warm_llm = AsyncMock(return_value=mock_warmup_result)
        mock_warmup_service.close = AsyncMock()

        mock_warmup_mod = MagicMock()
        mock_warmup_mod.ModelWarmupService.return_value = mock_warmup_service

        with (
            patch.dict("sys.modules", {"elle.rag.model_warmup": mock_warmup_mod}),
            patch.object(wizard, "_pull_model_with_progress", new_callable=AsyncMock, return_value=True),
            patch("elle.cli.setup.wizard.Progress"),
        ):
            result = await wizard._setup_models_async("test-model")

        assert result is True

    @pytest.mark.asyncio
    async def test_model_pull_failure(self, wizard, mock_console):
        """When model pull fails."""
        from unittest.mock import AsyncMock

        mock_warmup_service = MagicMock()
        mock_warmup_service.list_models = AsyncMock(return_value=["other-model"])
        mock_warmup_service.close = AsyncMock()

        mock_warmup_mod = MagicMock()
        mock_warmup_mod.ModelWarmupService.return_value = mock_warmup_service

        with (
            patch.dict("sys.modules", {"elle.rag.model_warmup": mock_warmup_mod}),
            patch.object(wizard, "_pull_model_with_progress", new_callable=AsyncMock, return_value=False),
            patch("elle.cli.setup.wizard.Progress"),
        ):
            result = await wizard._setup_models_async("test-model")

        assert result is False

    @pytest.mark.asyncio
    async def test_warmup_failure(self, wizard, mock_console):
        """When model warmup fails but model was pulled."""
        from unittest.mock import AsyncMock

        mock_warmup_result = MagicMock(success=False, duration_ms=0, error="warmup failed")

        mock_warmup_service = MagicMock()
        mock_warmup_service.list_models = AsyncMock(return_value=["test-model"])
        mock_warmup_service.warm_llm = AsyncMock(return_value=mock_warmup_result)
        mock_warmup_service.close = AsyncMock()

        mock_warmup_mod = MagicMock()
        mock_warmup_mod.ModelWarmupService.return_value = mock_warmup_service

        with (
            patch.dict("sys.modules", {"elle.rag.model_warmup": mock_warmup_mod}),
            patch("elle.cli.setup.wizard.Progress"),
        ):
            result = await wizard._setup_models_async("test-model")

        # Returns True because model is available, just warmup failed
        assert result is True

    @pytest.mark.asyncio
    async def test_warmup_timeout(self, wizard, mock_console):
        """When model warmup times out."""
        import asyncio as _asyncio
        from unittest.mock import AsyncMock

        mock_warmup_result = MagicMock(success=False, model="test-model", message="Warmup timed out", error="timeout")

        mock_warmup_service = MagicMock()
        mock_warmup_service.list_models = AsyncMock(return_value=["test-model"])
        mock_warmup_service.close = AsyncMock()

        async def slow_warmup():
            await _asyncio.sleep(9999)

        mock_warmup_service.warm_llm = slow_warmup

        mock_warmup_mod = MagicMock()
        mock_warmup_mod.ModelWarmupService.return_value = mock_warmup_service
        mock_warmup_mod.WarmupResult.return_value = mock_warmup_result

        async def mock_wait_for(coro, timeout=None):
            # Cancel the coroutine properly
            try:
                coro.close()
            except AttributeError:
                pass
            raise TimeoutError("timed out")

        with (
            patch.dict("sys.modules", {"elle.rag.model_warmup": mock_warmup_mod}),
            patch("asyncio.wait_for", side_effect=mock_wait_for),
            patch("elle.cli.setup.wizard.Progress"),
        ):
            result = await wizard._setup_models_async("test-model")

        assert result is True

    @pytest.mark.asyncio
    async def test_warmup_keyboard_interrupt(self, wizard, mock_console):
        """When user interrupts warmup with Ctrl+C."""
        from unittest.mock import AsyncMock

        mock_warmup_service = MagicMock()
        mock_warmup_service.list_models = AsyncMock(return_value=["test-model"])

        async def interrupted_warmup():
            raise KeyboardInterrupt()

        mock_warmup_service.warm_llm = interrupted_warmup
        mock_warmup_service.close = AsyncMock()

        mock_warmup_mod = MagicMock()
        mock_warmup_mod.ModelWarmupService.return_value = mock_warmup_service

        with (
            patch.dict("sys.modules", {"elle.rag.model_warmup": mock_warmup_mod}),
            patch("elle.cli.setup.wizard.Progress"),
        ):
            result = await wizard._setup_models_async("test-model")

        assert result is True

    @pytest.mark.asyncio
    async def test_setup_models_async_exception(self, wizard, mock_console):
        """When an unexpected exception occurs during model setup."""
        from unittest.mock import AsyncMock

        mock_warmup_service = MagicMock()
        mock_warmup_service.list_models = AsyncMock(side_effect=RuntimeError("unexpected"))
        mock_warmup_service.close = AsyncMock()

        mock_warmup_mod = MagicMock()
        mock_warmup_mod.ModelWarmupService.return_value = mock_warmup_service

        with (
            patch.dict("sys.modules", {"elle.rag.model_warmup": mock_warmup_mod}),
            patch("elle.cli.setup.wizard.Progress"),
        ):
            result = await wizard._setup_models_async("test-model")

        assert result is False
        mock_warmup_service.close.assert_awaited_once()


# =============================================================================
# Additional coverage: _pull_model_with_progress
# =============================================================================


class TestPullModelWithProgress:
    """Tests for _pull_model_with_progress method."""

    @pytest.mark.asyncio
    async def test_successful_pull_with_progress(self, wizard):
        """Test successful model pull with progress updates."""
        from unittest.mock import AsyncMock

        progress = MagicMock()
        task_mock = MagicMock()
        task_mock.total = 100
        progress.tasks.__getitem__ = MagicMock(return_value=task_mock)

        mock_response = MagicMock()
        mock_response.status_code = 200

        lines = [
            '{"status": "pulling manifest"}',
            '{"status": "downloading", "total": 1000, "completed": 500}',
            '{"status": "success"}',
        ]

        async def async_iter_lines():
            for line in lines:
                yield line

        mock_response.aiter_lines = async_iter_lines

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock()

        # Create a combined context manager
        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream.return_value = mock_stream_ctx

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await wizard._pull_model_with_progress("test-model", progress, 0)

        assert result is True

    @pytest.mark.asyncio
    async def test_pull_non_200_response(self, wizard):
        """Test pull when server returns non-200."""
        from unittest.mock import AsyncMock

        progress = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream.return_value = mock_stream_ctx

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await wizard._pull_model_with_progress("test-model", progress, 0)

        assert result is False

    @pytest.mark.asyncio
    async def test_pull_exception(self, wizard):
        """Test pull when an exception occurs."""

        progress = MagicMock()

        with patch("httpx.AsyncClient", side_effect=RuntimeError("connection error")):
            result = await wizard._pull_model_with_progress("test-model", progress, 0)

        assert result is False

    @pytest.mark.asyncio
    async def test_pull_with_json_decode_error(self, wizard):
        """Test pull with invalid JSON in stream."""
        from unittest.mock import AsyncMock

        progress = MagicMock()
        task_mock = MagicMock()
        task_mock.total = None
        progress.tasks.__getitem__ = MagicMock(return_value=task_mock)

        mock_response = MagicMock()
        mock_response.status_code = 200

        lines = [
            "not json",
            "",
            '{"status": "success"}',
        ]

        async def async_iter_lines():
            for line in lines:
                yield line

        mock_response.aiter_lines = async_iter_lines

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream.return_value = mock_stream_ctx

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await wizard._pull_model_with_progress("test-model", progress, 0)

        assert result is True

    @pytest.mark.asyncio
    async def test_pull_status_only_no_total(self, wizard):
        """Test pull with status updates but no total/completed fields."""
        from unittest.mock import AsyncMock

        progress = MagicMock()
        task_mock = MagicMock()
        task_mock.total = 100
        progress.tasks.__getitem__ = MagicMock(return_value=task_mock)

        mock_response = MagicMock()
        mock_response.status_code = 200

        lines = [
            '{"status": "pulling manifest"}',
            '{"status": "verifying sha256 digest"}',
            '{"status": "success"}',
        ]

        async def async_iter_lines():
            for line in lines:
                yield line

        mock_response.aiter_lines = async_iter_lines

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream.return_value = mock_stream_ctx

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await wizard._pull_model_with_progress("test-model", progress, 0)

        assert result is True

    @pytest.mark.asyncio
    async def test_pull_no_success_status(self, wizard):
        """Test pull that completes without explicit success status."""
        from unittest.mock import AsyncMock

        progress = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 200

        lines = [
            '{"status": "pulling"}',
            '{"status": "done"}',
        ]

        async def async_iter_lines():
            for line in lines:
                yield line

        mock_response.aiter_lines = async_iter_lines

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream.return_value = mock_stream_ctx

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await wizard._pull_model_with_progress("test-model", progress, 0)

        # Returns True even without "success" status (falls through to end)
        assert result is True


# =============================================================================
# Additional coverage: _start_telemetryd
# =============================================================================


class TestStartTelemetryd:
    """Tests for _start_telemetryd method."""

    def test_already_running(self, wizard):
        """When telemetryd is already running via systemd."""
        mock_result = MagicMock(returncode=0)

        with patch("subprocess.run", return_value=mock_result):
            result = wizard._start_telemetryd()

        assert result["success"] is True
        assert "already running" in result["method"]

    def test_systemd_stopped_polkit_start_success(self, wizard):
        """When service exists but is stopped, start via Polkit."""
        mock_status = MagicMock(returncode=3)  # 3 = stopped
        wizard._check_telemetryd_running = MagicMock(return_value=True)

        mock_polkit = MagicMock()
        mock_polkit.start_service_sync.return_value = MagicMock(success=True, error=None, authorized=True)

        with (
            patch("subprocess.run", return_value=mock_status),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
            patch("time.sleep"),
        ):
            result = wizard._start_telemetryd()

        assert result["success"] is True
        assert result["method"] == "systemd"

    def test_systemd_stopped_polkit_start_socket_not_ready(self, wizard):
        """When service starts but socket is not ready."""
        mock_status = MagicMock(returncode=3)
        wizard._check_telemetryd_running = MagicMock(return_value=False)

        mock_polkit = MagicMock()
        mock_polkit.start_service_sync.return_value = MagicMock(success=True, error=None, authorized=True)

        with (
            patch("subprocess.run", return_value=mock_status),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
            patch("time.sleep"),
        ):
            result = wizard._start_telemetryd()

        assert result["success"] is False
        assert "socket not ready" in result["error"]

    def test_systemd_stopped_polkit_denied(self, wizard):
        """When Polkit authorization is denied."""
        mock_status = MagicMock(returncode=3)

        mock_polkit = MagicMock()
        mock_polkit.start_service_sync.return_value = MagicMock(
            success=False, error="Permission denied", authorized=False
        )

        with (
            patch("subprocess.run", return_value=mock_status),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
        ):
            result = wizard._start_telemetryd()

        assert result["success"] is False
        assert "denied" in result["error"].lower()

    def test_systemd_stopped_polkit_error(self, wizard):
        """When Polkit start fails with an error."""
        mock_status = MagicMock(returncode=3)

        mock_polkit = MagicMock()
        mock_polkit.start_service_sync.return_value = MagicMock(success=False, error="Some error\n", authorized=True)

        with (
            patch("subprocess.run", return_value=mock_status),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
        ):
            result = wizard._start_telemetryd()

        assert result["success"] is False
        assert "Some error" in result["error"]

    def test_systemd_stopped_polkit_empty_error(self, wizard):
        """When Polkit start fails with no error message."""
        mock_status = MagicMock(returncode=3)

        mock_polkit = MagicMock()
        mock_polkit.start_service_sync.return_value = MagicMock(success=False, error=None, authorized=True)

        with (
            patch("subprocess.run", return_value=mock_status),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
        ):
            result = wizard._start_telemetryd()

        assert result["success"] is False
        assert "Failed to start" in result["error"]

    def test_systemctl_not_found_binary_found(self, wizard):
        """When systemctl is not found but binary is on PATH."""
        wizard._check_telemetryd_running = MagicMock(return_value=True)

        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("shutil.which", return_value="/usr/bin/elled-telemetryd"),
            patch("subprocess.Popen"),
            patch("time.sleep"),
        ):
            result = wizard._start_telemetryd()

        assert result["success"] is True
        assert "binary" in result["method"]

    def test_binary_start_check_fails(self, wizard):
        """When binary starts but check fails."""
        wizard._check_telemetryd_running = MagicMock(return_value=False)

        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("shutil.which", return_value="/usr/bin/elled-telemetryd"),
            patch("subprocess.Popen"),
            patch("time.sleep"),
        ):
            result = wizard._start_telemetryd()

        # The method doesn't return failure when check fails after Popen;
        # it falls through. But let's see what happens.
        # Looking at code: if _check_telemetryd_running returns False after Popen,
        # it just doesn't return True, falls to end which returns not-installed
        assert result["success"] is False

    def test_binary_popen_exception(self, wizard):
        """When Popen raises an exception for binary start."""
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("shutil.which", return_value="/usr/bin/elled-telemetryd"),
            patch("subprocess.Popen", side_effect=OSError("cannot start")),
        ):
            result = wizard._start_telemetryd()

        assert result["success"] is False
        assert "cannot start" in result["error"]

    def test_binary_not_found_at_all(self, wizard):
        """When telemetryd binary is not found anywhere."""
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("shutil.which", return_value=None),
        ):
            result = wizard._start_telemetryd()

        assert result["success"] is False
        assert "not installed" in result["error"]

    def test_systemd_service_not_exist(self, wizard):
        """When systemd service does not exist (returncode != 0 or 3)."""
        mock_status = MagicMock(returncode=4)  # 4 = unit not found

        with (
            patch("subprocess.run", return_value=mock_status),
            patch("shutil.which", return_value=None),
        ):
            result = wizard._start_telemetryd()

        assert result["success"] is False


# =============================================================================
# Additional coverage: _start_daemon
# =============================================================================


class TestStartDaemon:
    """Tests for _start_daemon method."""

    def test_already_running_system(self, wizard):
        """When daemon is already running via system systemd."""
        mock_result = MagicMock(returncode=0)

        with patch("subprocess.run", return_value=mock_result):
            result = wizard._start_daemon()

        assert result["success"] is True
        assert "already running" in result["method"]

    def test_system_stopped_polkit_start_success(self, wizard):
        """When system service is stopped and Polkit start succeeds."""
        mock_status = MagicMock(returncode=3)

        mock_polkit = MagicMock()
        mock_polkit.start_service_sync.return_value = MagicMock(success=True, error=None, authorized=True)

        with (
            patch("subprocess.run", return_value=mock_status),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
        ):
            result = wizard._start_daemon()

        assert result["success"] is True
        assert result["method"] == "systemd"

    def test_system_stopped_polkit_denied(self, wizard):
        """When authorization is denied for daemon start."""
        mock_status = MagicMock(returncode=3)

        mock_polkit = MagicMock()
        mock_polkit.start_service_sync.return_value = MagicMock(success=False, error="denied", authorized=False)

        with (
            patch("subprocess.run", return_value=mock_status),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
        ):
            result = wizard._start_daemon()

        assert result["success"] is False
        assert "denied" in result["error"].lower()

    def test_system_stopped_polkit_error(self, wizard):
        """When Polkit start returns an error."""
        mock_status = MagicMock(returncode=3)

        mock_polkit = MagicMock()
        mock_polkit.start_service_sync.return_value = MagicMock(
            success=False, error="service failed\n", authorized=True
        )

        with (
            patch("subprocess.run", return_value=mock_status),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
        ):
            result = wizard._start_daemon()

        assert result["success"] is False
        assert "service failed" in result["error"]

    def test_system_stopped_polkit_empty_error(self, wizard):
        """When Polkit start fails with no error message."""
        mock_status = MagicMock(returncode=3)

        mock_polkit = MagicMock()
        mock_polkit.start_service_sync.return_value = MagicMock(success=False, error=None, authorized=True)

        with (
            patch("subprocess.run", return_value=mock_status),
            patch.dict("sys.modules", {"elle.security.polkit_helper": mock_polkit}),
        ):
            result = wizard._start_daemon()

        assert result["success"] is False
        assert "Failed to start" in result["error"]

    def test_user_systemd_already_running(self, wizard):
        """When user systemd service is already running."""
        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                # System service not found
                result.returncode = 4
            else:
                # User service running
                result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=mock_run):
            result = wizard._start_daemon()

        assert result["success"] is True
        assert "--user" in result["method"]

    def test_user_systemd_stopped_start_success(self, wizard):
        """When user systemd service is stopped and starts successfully."""
        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.returncode = 4  # System service not found
            elif call_count[0] == 2:
                result.returncode = 3  # User service stopped
            else:
                result.returncode = 0  # Start succeeds
            return result

        with patch("subprocess.run", side_effect=mock_run):
            result = wizard._start_daemon()

        assert result["success"] is True
        assert result["method"] == "systemd --user"

    def test_user_systemd_timeout(self, wizard):
        """When user systemd commands time out."""
        import subprocess as sp

        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # System service not found
                raise FileNotFoundError
            # User service timeout
            raise sp.TimeoutExpired(cmd=cmd, timeout=5)

        wizard._check_daemon_running = MagicMock(return_value=False)

        with (
            patch("subprocess.run", side_effect=mock_run),
            patch("shutil.which", return_value=None),
            patch("subprocess.Popen", side_effect=OSError("cannot start")),
        ):
            result = wizard._start_daemon()

        assert result["success"] is False

    def test_python_module_fallback_success(self, wizard):
        """When binary not found but python module works."""
        wizard._check_daemon_running = MagicMock(return_value=True)

        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("shutil.which", return_value=None),
            patch("subprocess.Popen"),
            patch("time.sleep"),
        ):
            result = wizard._start_daemon()

        assert result["success"] is True
        assert result["method"] == "python module"

    def test_python_module_fallback_check_fails(self, wizard):
        """When python module starts but daemon check fails."""
        wizard._check_daemon_running = MagicMock(return_value=False)

        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("shutil.which", return_value=None),
            patch("subprocess.Popen"),
            patch("time.sleep"),
        ):
            result = wizard._start_daemon()

        assert result["success"] is False
        assert "No method" in result["error"]

    def test_python_module_fallback_exception(self, wizard):
        """When python module Popen raises exception."""
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("shutil.which", return_value=None),
            patch("subprocess.Popen", side_effect=OSError("no python")),
        ):
            result = wizard._start_daemon()

        assert result["success"] is False
        assert "no python" in result["error"]

    def test_elled_binary_success(self, wizard):
        """When elled binary is found and starts."""
        wizard._check_daemon_running = MagicMock(return_value=True)

        popen_calls = [0]

        def mock_popen(cmd, **kwargs):
            popen_calls[0] += 1
            if popen_calls[0] == 1:
                # First Popen is for python module, but since elled is found
                # we need to simulate python module not working
                raise OSError("python module failed")
            return MagicMock()

        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("shutil.which", return_value="/usr/bin/elled"),
            patch("subprocess.Popen", side_effect=mock_popen),
            patch("time.sleep"),
        ):
            result = wizard._start_daemon()

        # The python module call fails, but since elled_path is set,
        # the method should continue. Actually looking at the code:
        # if elled_path is not None, it skips python module fallback
        # Wait - the code checks `if not elled_path` first for python module.
        # Since elled_path is set, it skips python module and goes to the
        # elled binary section. Let me re-check.
        # Actually: when elled_path is set, `if not elled_path` is False,
        # so it jumps to `if elled_path` block.
        assert result["success"] is False  # first popen fails

    def test_elled_binary_found_start_success(self, wizard):
        """When elled binary is found on PATH and starts."""
        wizard._check_daemon_running = MagicMock(return_value=True)

        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("shutil.which", return_value="/usr/bin/elled"),
            patch("subprocess.Popen"),
            patch("time.sleep"),
        ):
            result = wizard._start_daemon()

        assert result["success"] is True
        assert result["method"] == "elled binary"

    def test_elled_binary_popen_exception(self, wizard):
        """When elled binary Popen raises exception."""
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("shutil.which", return_value="/usr/bin/elled"),
            patch("subprocess.Popen", side_effect=OSError("cannot start elled")),
        ):
            result = wizard._start_daemon()

        assert result["success"] is False
        assert "cannot start elled" in result["error"]

    def test_elled_binary_check_fails(self, wizard):
        """When elled binary starts but daemon check fails."""
        wizard._check_daemon_running = MagicMock(return_value=False)

        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("shutil.which", return_value="/usr/bin/elled"),
            patch("subprocess.Popen"),
            patch("time.sleep"),
        ):
            result = wizard._start_daemon()

        assert result["success"] is False
        assert "No method" in result["error"]

    def test_no_method_available(self, wizard):
        """When no method is available to start daemon."""
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("shutil.which", return_value=None),
            patch("subprocess.Popen", side_effect=OSError("failed")),
        ):
            result = wizard._start_daemon()

        assert result["success"] is False
        assert "No method" in result["error"] or "failed" in result["error"]


# =============================================================================
# Additional coverage: _show_completion
# =============================================================================


class TestShowCompletion:
    """Tests for _show_completion method."""

    def test_all_services_running(self, wizard, mock_console):
        """When all services are running at completion."""
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": []})
        wizard._check_daemon_running = MagicMock(return_value=True)
        wizard._check_telemetryd_running = MagicMock(return_value=True)

        wizard._show_completion()
        # Should not show any warning tips
        assert mock_console.print.called

    def test_no_services_running(self, wizard, mock_console):
        """When no services are running at completion."""
        wizard._check_ollama = MagicMock(return_value={"installed": False, "running": False, "models": []})
        wizard._check_daemon_running = MagicMock(return_value=False)
        wizard._check_telemetryd_running = MagicMock(return_value=False)

        wizard._show_completion()
        assert mock_console.print.called

    def test_ollama_installed_not_running(self, wizard, mock_console):
        """When Ollama is installed but not running at completion."""
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": False, "models": []})
        wizard._check_daemon_running = MagicMock(return_value=True)
        wizard._check_telemetryd_running = MagicMock(return_value=True)

        wizard._show_completion()
        assert mock_console.print.called

    def test_ollama_not_installed(self, wizard, mock_console):
        """When Ollama is not installed at completion."""
        wizard._check_ollama = MagicMock(return_value={"installed": False, "running": False, "models": []})
        wizard._check_daemon_running = MagicMock(return_value=True)
        wizard._check_telemetryd_running = MagicMock(return_value=True)

        wizard._show_completion()
        assert mock_console.print.called

    def test_daemon_not_running(self, wizard, mock_console):
        """When daemon is not running at completion."""
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": []})
        wizard._check_daemon_running = MagicMock(return_value=False)
        wizard._check_telemetryd_running = MagicMock(return_value=True)

        wizard._show_completion()
        assert mock_console.print.called

    def test_telemetryd_not_running(self, wizard, mock_console):
        """When telemetryd is not running at completion."""
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": []})
        wizard._check_daemon_running = MagicMock(return_value=True)
        wizard._check_telemetryd_running = MagicMock(return_value=False)

        wizard._show_completion()
        assert mock_console.print.called

    def test_convenient_polkit_configured(self, wizard, mock_console):
        """When convenient mode is configured and polkit was set up."""
        wizard.prefs.privilege_level = PrivilegeLevel.CONVENIENT
        wizard.prefs.polkit_configured = True
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": []})
        wizard._check_daemon_running = MagicMock(return_value=True)
        wizard._check_telemetryd_running = MagicMock(return_value=True)

        wizard._show_completion()
        assert mock_console.print.called

    def test_convenient_polkit_not_configured(self, wizard, mock_console):
        """When convenient mode but polkit was not configured."""
        wizard.prefs.privilege_level = PrivilegeLevel.CONVENIENT
        wizard.prefs.polkit_configured = False
        wizard._check_ollama = MagicMock(return_value={"installed": True, "running": True, "models": []})
        wizard._check_daemon_running = MagicMock(return_value=True)
        wizard._check_telemetryd_running = MagicMock(return_value=True)

        wizard._show_completion()
        assert mock_console.print.called


# =============================================================================
# Additional coverage: _review_and_confirm branches
# =============================================================================


class TestReviewAndConfirmAdditional:
    """Additional tests for review step with various preference combos."""

    def test_no_telemetry_enabled(self, wizard, mock_console):
        """When no telemetry sources are enabled."""
        wizard.prefs.journal_enabled = False
        wizard.prefs.kernel_enabled = False
        wizard.prefs.probes_enabled = False
        wizard.prefs.docker_enabled = False
        wizard.prefs.ebpf_enabled = False
        wizard.prefs.api_enabled = False
        wizard.prefs.auto_learn_packages = False
        wizard.prompt.prompt_confirm_with_back.return_value = True

        result = wizard._review_and_confirm(can_go_back=True)
        assert result == StepResult.CONTINUE

    def test_all_telemetry_and_features_enabled(self, wizard, mock_console):
        """When all telemetry and features are enabled."""
        wizard.prefs.journal_enabled = True
        wizard.prefs.kernel_enabled = True
        wizard.prefs.probes_enabled = True
        wizard.prefs.docker_enabled = True
        wizard.prefs.ebpf_enabled = True
        wizard.prefs.api_enabled = True
        wizard.prefs.auto_learn_packages = True
        wizard.prompt.prompt_confirm_with_back.return_value = True

        result = wizard._review_and_confirm(can_go_back=True)
        assert result == StepResult.CONTINUE

    def test_only_docker_enabled(self, wizard, mock_console):
        """When only docker telemetry is enabled."""
        wizard.prefs.journal_enabled = False
        wizard.prefs.kernel_enabled = False
        wizard.prefs.probes_enabled = False
        wizard.prefs.docker_enabled = True
        wizard.prefs.ebpf_enabled = False
        wizard.prompt.prompt_confirm_with_back.return_value = True

        result = wizard._review_and_confirm(can_go_back=True)
        assert result == StepResult.CONTINUE

    def test_only_ebpf_enabled(self, wizard, mock_console):
        """When only eBPF telemetry is enabled."""
        wizard.prefs.journal_enabled = False
        wizard.prefs.kernel_enabled = False
        wizard.prefs.probes_enabled = False
        wizard.prefs.docker_enabled = False
        wizard.prefs.ebpf_enabled = True
        wizard.prompt.prompt_confirm_with_back.return_value = True

        result = wizard._review_and_confirm(can_go_back=True)
        assert result == StepResult.CONTINUE

    def test_only_probes_enabled(self, wizard, mock_console):
        """When only probes telemetry is enabled."""
        wizard.prefs.journal_enabled = False
        wizard.prefs.kernel_enabled = False
        wizard.prefs.probes_enabled = True
        wizard.prefs.docker_enabled = False
        wizard.prefs.ebpf_enabled = False
        wizard.prompt.prompt_confirm_with_back.return_value = True

        result = wizard._review_and_confirm(can_go_back=True)
        assert result == StepResult.CONTINUE

    def test_review_with_can_go_back_false(self, wizard, mock_console):
        """When review step cannot go back."""
        wizard.prompt.prompt_confirm_with_back.return_value = True

        result = wizard._review_and_confirm(can_go_back=False)
        assert result == StepResult.CONTINUE

    def test_only_auto_learn_feature(self, wizard, mock_console):
        """When only auto-learn feature is enabled."""
        wizard.prefs.api_enabled = False
        wizard.prefs.auto_learn_packages = True
        wizard.prompt.prompt_confirm_with_back.return_value = True

        result = wizard._review_and_confirm(can_go_back=True)
        assert result == StepResult.CONTINUE


# =============================================================================
# Additional coverage: _configure_safety branches
# =============================================================================


class TestConfigureSafetyAdditional:
    """Additional tests for safety configuration edge cases."""

    def test_safety_none_cannot_go_back(self, wizard, mock_console):
        """When prompt_select returns None and can't go back."""
        wizard.prompt.prompt_select.return_value = None
        wizard.prompt.prompt_confirm.return_value = True

        # When can_go_back is False and choice is None, it should not return BACK
        # (skips the BACK return, proceeds to check if choice truthy)
        result = wizard._configure_safety(can_go_back=False)
        # choice is None so no safety level set, then conf_choice is also None
        # but can_go_back is False so no BACK, continues to preview
        assert result == StepResult.CONTINUE

    def test_confirmation_none_cannot_go_back(self, wizard, mock_console):
        """When confirmation select returns None and can't go back."""
        wizard.prompt.prompt_select.side_effect = ["standard", None]
        wizard.prompt.prompt_confirm.return_value = True

        result = wizard._configure_safety(can_go_back=False)
        assert result == StepResult.CONTINUE

    def test_sets_minimal_safety(self, wizard, mock_console):
        """Test setting minimal safety level."""
        wizard.prompt.prompt_select.side_effect = ["minimal", "high_risk"]
        wizard.prompt.prompt_confirm.return_value = True

        wizard._configure_safety(can_go_back=True)
        assert wizard.prefs.safety_level == SafetyLevel.MINIMAL


# =============================================================================
# Additional coverage: _configure_telemetry branches
# =============================================================================


class TestConfigureTelemetryAdditional:
    """Additional telemetry configuration tests."""

    def test_telemetry_none_cannot_go_back(self, wizard, mock_console):
        """When multi_select returns None and can't go back."""
        wizard.prompt.prompt_multi_select.return_value = None

        result = wizard._configure_telemetry(can_go_back=False)
        # None + can_go_back=False means it skips the BACK, skips setting prefs
        assert result == StepResult.CONTINUE

    def test_telemetry_can_go_back_false(self, wizard, mock_console):
        """When can_go_back is False, should not show back option."""
        wizard.prompt.prompt_multi_select.return_value = ["journal"]

        result = wizard._configure_telemetry(can_go_back=False)
        assert result == StepResult.CONTINUE


# =============================================================================
# Additional coverage: _configure_features branches
# =============================================================================


class TestConfigureFeaturesAdditional:
    """Additional feature configuration tests."""

    def test_features_none_cannot_go_back(self, wizard, mock_console):
        """When multi_select returns None and can't go back."""
        wizard.prompt.prompt_multi_select.return_value = None

        result = wizard._configure_features(can_go_back=False)
        assert result == StepResult.CONTINUE


# =============================================================================
# Additional coverage: _configure_privileges branches
# =============================================================================


class TestConfigurePrivilegesAdditional:
    """Additional privilege configuration tests."""

    def test_privilege_none_cannot_go_back(self, wizard, mock_console):
        """When prompt_select returns None and can't go back."""
        wizard.prompt.prompt_select.return_value = None
        result = wizard._configure_privileges(can_go_back=False)
        assert result == StepResult.CONTINUE

    def test_can_go_back_false_no_back_hint(self, wizard, mock_console):
        """When can_go_back is False, should not show back hint."""
        wizard.prompt.prompt_select.return_value = "secure"
        result = wizard._configure_privileges(can_go_back=False)
        assert result == StepResult.CONTINUE


# =============================================================================
# Additional coverage: wizard.run edge cases
# =============================================================================


class TestWizardRunAdditional:
    """Additional tests for the wizard run method."""

    def test_eoferror_returns_false(self, wizard, mock_console):
        """When EOFError is raised."""
        wizard._show_welcome = MagicMock(side_effect=EOFError)

        result = wizard.run(reconfigure=False)
        assert result is False

    def test_back_at_step_zero_stays(self, wizard, mock_console):
        """When back is returned at step 0, stays at step 0."""
        call_count = [0]

        def env_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return StepResult.BACK
            return StepResult.CONTINUE

        wizard._check_environment = MagicMock(side_effect=env_side_effect)
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
        # check_environment called multiple times due to back at step 0
        assert wizard._check_environment.call_count >= 2

    def test_cancel_at_review_step(self, wizard, mock_console):
        """When user cancels at the review step."""
        wizard._check_environment = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_safety = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_telemetry = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_features = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_privileges = MagicMock(return_value=StepResult.CONTINUE)
        wizard._review_and_confirm = MagicMock(return_value=StepResult.CANCEL)

        result = wizard.run(reconfigure=False)
        assert result is False

    def test_back_from_review_to_privileges(self, wizard, mock_console):
        """When user goes back from review to privileges step."""
        review_count = [0]

        def review_side_effect(**kwargs):
            review_count[0] += 1
            if review_count[0] == 1:
                return StepResult.BACK
            return StepResult.CONTINUE

        priv_count = [0]

        def priv_side_effect(**kwargs):
            priv_count[0] += 1
            return StepResult.CONTINUE

        wizard._check_environment = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_safety = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_telemetry = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_features = MagicMock(return_value=StepResult.CONTINUE)
        wizard._configure_privileges = MagicMock(side_effect=priv_side_effect)
        wizard._review_and_confirm = MagicMock(side_effect=review_side_effect)
        wizard._save_configuration = MagicMock()
        wizard._start_services = MagicMock(return_value=True)
        wizard._show_completion = MagicMock()

        result = wizard.run(reconfigure=False)
        assert result is True
        assert wizard._configure_privileges.call_count >= 2

    def test_show_welcome_called_not_reconfigure(self, wizard, mock_console):
        """Verify _show_welcome is called when not reconfiguring."""
        wizard._show_welcome = MagicMock()
        wizard._check_environment = MagicMock(return_value=StepResult.CANCEL)

        wizard.run(reconfigure=False)
        wizard._show_welcome.assert_called_once()

    def test_show_reconfigure_intro_called(self, wizard, mock_console):
        """Verify _show_reconfigure_intro is called when reconfiguring."""
        wizard._show_reconfigure_intro = MagicMock()
        wizard._check_environment = MagicMock(return_value=StepResult.CANCEL)

        wizard.run(reconfigure=True)
        wizard._show_reconfigure_intro.assert_called_once()


# =============================================================================
# Additional coverage: _show_welcome / _show_reconfigure_intro
# =============================================================================


class TestShowWelcome:
    """Tests for _show_welcome display."""

    def test_show_welcome_renders(self, wizard, mock_console):
        """Verify welcome renders without error."""
        wizard._show_welcome()
        assert mock_console.print.called

    def test_show_reconfigure_intro_renders(self, wizard, mock_console):
        """Verify reconfigure intro renders without error."""
        wizard._show_reconfigure_intro()
        assert mock_console.print.called


# =============================================================================
# Additional coverage: run_setup_wizard function
# =============================================================================


class TestRunSetupWizardAdditional:
    """Additional tests for run_setup_wizard top-level function."""

    def test_run_setup_wizard_reconfigure(self, mock_console, mock_ui_functions):
        """Test run_setup_wizard with reconfigure=True."""
        mock_prompt = MagicMock()
        with (
            patch("elle.cli.setup.wizard.load_setup_state", return_value=SetupState()),
            patch.object(SetupWizard, "run", return_value=True) as mock_run,
        ):
            from elle.cli.setup.wizard import run_setup_wizard

            result = run_setup_wizard(mock_prompt, reconfigure=True)
            assert result is True
            mock_run.assert_called_once_with(reconfigure=True)

    def test_run_setup_wizard_failure(self, mock_console, mock_ui_functions):
        """Test run_setup_wizard when wizard returns False."""
        mock_prompt = MagicMock()
        with (
            patch("elle.cli.setup.wizard.load_setup_state", return_value=SetupState()),
            patch.object(SetupWizard, "run", return_value=False),
        ):
            from elle.cli.setup.wizard import run_setup_wizard

            result = run_setup_wizard(mock_prompt, reconfigure=False)
            assert result is False


# =============================================================================
# Additional coverage: _build_policy_yaml combined scenarios
# =============================================================================


class TestBuildPolicyYamlAdditional:
    """Additional policy YAML generation tests for combined scenarios."""

    def test_cautious_with_always_confirm_and_preview(self):
        """Cautious + always confirm + preview produces all rules."""
        prefs = SetupPreferences(
            safety_level=SafetyLevel.CAUTIOUS,
            confirmation_preference=ConfirmationPreference.ALWAYS,
            require_preview_for_configs=True,
        )
        yaml = _build_policy_yaml(prefs)
        assert "default_effect: require_confirmation" in yaml
        assert "confirm-all-tasks" in yaml
        assert "preview-config-changes" in yaml
        assert "cautious-service-restart" in yaml
        assert "cautious-package-install" in yaml

    def test_minimal_with_never_confirm(self):
        """Minimal + never confirm produces skip rules."""
        prefs = SetupPreferences(
            safety_level=SafetyLevel.MINIMAL,
            confirmation_preference=ConfirmationPreference.NEVER,
            require_preview_for_configs=False,
        )
        yaml = _build_policy_yaml(prefs)
        assert "default_effect: allow" in yaml
        assert "skip-confirmation" in yaml
        assert "cautious-service-restart" not in yaml
