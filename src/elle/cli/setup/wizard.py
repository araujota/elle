"""ELLE Setup Wizard.

A friendly, interactive wizard that guides users through initial configuration.
Runs on first launch and can be re-run via /reconfigure or /policies.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
)
from rich.text import Text

from elle.cli.setup.models import (
    CONFIRMATION_INFO,
    FEATURE_INFO,
    PRIVILEGE_LEVEL_INFO,
    SAFETY_LEVEL_INFO,
    TELEMETRY_INFO,
    ConfirmationPreference,
    PrivilegeLevel,
    SafetyLevel,
    SetupPreferences,
    SetupState,
)
from elle.cli.ui import (
    Icons,
    bulleted_list,
    console,
    numbered_list,
    print_muted,
    print_success,
    print_warning,
    section_header,
    section_rule,
    tip,
)

if TYPE_CHECKING:
    from elle.cli.ui.prompt import EllePrompt

logger = logging.getLogger(__name__)


class StepResult(str, Enum):
    """Result of a wizard step."""

    CONTINUE = "continue"  # Move to next step
    BACK = "back"  # Go back to previous step
    CANCEL = "cancel"  # Cancel the wizard


# Config file paths
USER_CONFIG_DIR = Path.home() / ".config" / "elle"
USER_CONFIG_FILE = USER_CONFIG_DIR / "elle.toml"
USER_POLICY_FILE = USER_CONFIG_DIR / "policy.yaml"

# State marker in config
SETUP_STATE_KEY = "setup"


def is_first_run() -> bool:
    """Check if this is the first time ELLE is being run.

    Returns:
        True if setup wizard should run.
    """
    if not USER_CONFIG_FILE.exists():
        return True

    try:
        import tomllib

        with open(USER_CONFIG_FILE, "rb") as f:
            config = tomllib.load(f)

        setup = config.get("setup", {})
        return not setup.get("completed", False)
    except Exception as e:
        logger.debug(f"Error checking first run status: {e}")
        return True


def load_setup_state() -> SetupState:
    """Load existing setup state from config.

    Returns:
        SetupState with current configuration.
    """
    if not USER_CONFIG_FILE.exists():
        return SetupState()

    try:
        import tomllib

        with open(USER_CONFIG_FILE, "rb") as f:
            config = tomllib.load(f)

        setup_data = config.get("setup", {})
        prefs_data = setup_data.get("preferences", {})

        prefs = SetupPreferences(**prefs_data) if prefs_data else SetupPreferences()

        return SetupState(
            completed=setup_data.get("completed", False),
            completed_at=setup_data.get("completed_at"),
            version=setup_data.get("version", "0.1.0"),
            preferences=prefs,
        )
    except Exception as e:
        logger.debug(f"Error loading setup state: {e}")
        return SetupState()


def save_setup_state(state: SetupState) -> None:
    """Save setup state to config file.

    Args:
        state: The setup state to save.
    """
    import toml

    # Ensure config directory exists
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing config or start fresh
    config: dict[str, Any] = {}
    if USER_CONFIG_FILE.exists():
        try:
            import tomllib

            with open(USER_CONFIG_FILE, "rb") as f:
                config = tomllib.load(f)
        except Exception as e:
            logger.debug(f"Error loading existing config: {e}")

    # Update setup section
    # Pydantic v1/v2 compatibility: try model_dump() first, fall back to dict()
    try:
        prefs_dict = state.preferences.model_dump()
    except AttributeError:
        prefs_dict = state.preferences.dict()

    config["setup"] = {
        "completed": state.completed,
        "completed_at": state.completed_at.isoformat() if state.completed_at else None,
        "version": state.version,
        "preferences": prefs_dict,
    }

    # Update daemon section based on preferences
    if "daemon" not in config:
        config["daemon"] = {}

    prefs = state.preferences
    config["daemon"]["journal_enabled"] = prefs.journal_enabled
    config["daemon"]["kernel_enabled"] = prefs.kernel_enabled
    config["daemon"]["probes_enabled"] = prefs.probes_enabled
    config["daemon"]["ebpf_enabled"] = prefs.ebpf_enabled
    config["daemon"]["docker_enabled"] = prefs.docker_enabled
    config["daemon"]["auto_learn_new_packages"] = prefs.auto_learn_packages

    if "daemon.api" not in config:
        config["daemon"]["api"] = {}
    config["daemon"]["api"]["enabled"] = prefs.api_enabled

    # Write config
    with open(USER_CONFIG_FILE, "w") as f:
        toml.dump(config, f)


def generate_policy_file(prefs: SetupPreferences) -> None:
    """Generate user policy file based on preferences.

    Args:
        prefs: User preferences from setup.
    """
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    policy_content = _build_policy_yaml(prefs)

    with open(USER_POLICY_FILE, "w") as f:
        f.write(policy_content)


def configure_polkit_privileges(prefs: SetupPreferences) -> tuple[bool, str]:
    """Configure Polkit privileges based on user preference.

    Args:
        prefs: User preferences.

    Returns:
        Tuple of (success, message).
    """
    import getpass
    import grp
    import subprocess

    if prefs.privilege_level == PrivilegeLevel.SECURE:
        # Nothing to do for secure mode (default Polkit behavior)
        return True, "Using default secure (password) mode."

    username = getpass.getuser()
    rules_dir = Path("/etc/polkit-1/rules.d")
    rules_file = rules_dir / "50-elle.rules"

    # Build the rules file content based on privilege level
    if prefs.privilege_level == PrivilegeLevel.CONVENIENT:
        # Group-based: members of 'elle' group get passwordless access
        rules_content = _build_group_rules()
    elif prefs.privilege_level == PrivilegeLevel.PASSWORDLESS:
        # User-based: specific user gets passwordless access
        rules_content = _build_user_rules(username)
    else:
        return False, f"Unknown privilege level: {prefs.privilege_level}"

    # Write rules file (requires root)
    try:
        # Create temp file and use sudo to copy it
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".rules", delete=False) as tmp:
            tmp.write(rules_content)
            tmp_path = tmp.name

        # Copy to polkit rules directory
        result = subprocess.run(
            ["sudo", "cp", tmp_path, str(rules_file)],
            capture_output=True,
            text=True,
        )

        # Clean up temp file
        Path(tmp_path).unlink(missing_ok=True)

        if result.returncode != 0:
            return False, f"Failed to install Polkit rules: {result.stderr}"

        # Set permissions
        subprocess.run(
            ["sudo", "chmod", "644", str(rules_file)],
            capture_output=True,
        )

    except Exception as e:
        return False, f"Failed to configure Polkit: {e}"

    # For convenient mode, also create group and add user
    if prefs.privilege_level == PrivilegeLevel.CONVENIENT:
        # Create 'elle' group if it doesn't exist
        try:
            grp.getgrnam("elle")
        except KeyError:
            result = subprocess.run(
                ["sudo", "groupadd", "elle"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 and "already exists" not in result.stderr:
                return False, f"Failed to create 'elle' group: {result.stderr}"

        # Add user to group
        result = subprocess.run(
            ["sudo", "usermod", "-aG", "elle", username],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False, f"Failed to add user to 'elle' group: {result.stderr}"

        return True, (
            f"Polkit rules installed. User '{username}' added to 'elle' group.\n"
            "Please log out and back in for group membership to take effect."
        )

    return True, "Polkit rules installed for passwordless mode."


def _build_group_rules() -> str:
    """Build Polkit rules for group-based authentication.

    Returns:
        JavaScript rules file content.
    """
    return """\
// ELLE Polkit Rules - Group-based authentication
// Members of the 'elle' group can perform ELLE operations without password
// Generated by ELLE setup wizard

polkit.addRule(function(action, subject) {
    // Check if this is an ELLE action
    if (action.id.indexOf("com.elle.") === 0) {
        // Allow if user is in the 'elle' group
        if (subject.isInGroup("elle")) {
            return polkit.Result.YES;
        }
    }
    return polkit.Result.NOT_HANDLED;
});
"""


def _build_user_rules(username: str) -> str:
    """Build Polkit rules for user-based passwordless authentication.

    Args:
        username: The username to grant access to.

    Returns:
        JavaScript rules file content.
    """
    return f"""\
// ELLE Polkit Rules - Passwordless mode for user '{username}'
// WARNING: This allows privileged ELLE operations without authentication
// Generated by ELLE setup wizard

polkit.addRule(function(action, subject) {{
    // Check if this is an ELLE action
    if (action.id.indexOf("com.elle.") === 0) {{
        // Allow for the configured user
        if (subject.user === "{username}") {{
            return polkit.Result.YES;
        }}
    }}
    return polkit.Result.NOT_HANDLED;
}});
"""


def _build_policy_yaml(prefs: SetupPreferences) -> str:
    """Build policy YAML content based on preferences.

    Args:
        prefs: User preferences.

    Returns:
        YAML content string.
    """
    lines = [
        "# ELLE Policy Configuration",
        "# Generated by setup wizard",
        f"# Last updated: {datetime.now().isoformat()}",
        "",
        "version: '1.0'",
        "name: User Policy",
        "extends: system:defaults",
        "",
        "# Default effect when no rules match",
    ]

    # Set default effect based on safety level (must be lowercase)
    if prefs.safety_level == SafetyLevel.CAUTIOUS:
        lines.append("default_effect: require_confirmation")
    elif prefs.safety_level == SafetyLevel.MINIMAL:
        lines.append("default_effect: allow")
    else:
        lines.append("default_effect: allow")

    lines.extend(["", "rules:"])

    # Add confirmation rules based on preference
    if prefs.confirmation_preference == ConfirmationPreference.ALWAYS:
        lines.extend(
            [
                "  - id: confirm-all-tasks",
                "    name: Require confirmation for all tasks",
                "    conditions:",
                "      - intent: system_task",
                "        match_type: exact",
                "    effect: require_confirmation",
                "    message: Please confirm this operation",
                "    priority: 50",
                "",
            ]
        )
    elif prefs.confirmation_preference == ConfirmationPreference.NEVER:
        lines.extend(
            [
                "  - id: skip-confirmation",
                "    name: Skip confirmation for low-risk tasks",
                "    conditions:",
                "      - risk_level: low",
                "        match_type: exact",
                "      - risk_level: medium",
                "        match_type: exact",
                "    effect: allow",
                "    priority: 40",
                "",
            ]
        )

    # Add preview requirement for config changes
    if prefs.require_preview_for_configs:
        lines.extend(
            [
                "  - id: preview-config-changes",
                "    name: Show preview before config changes",
                "    conditions:",
                "      - path: /etc/*",
                "        match_type: glob",
                "    effect: require_preview",
                "    message: Review changes before applying",
                "    priority: 45",
                "",
            ]
        )

    # Add stricter rules for cautious mode
    if prefs.safety_level == SafetyLevel.CAUTIOUS:
        lines.extend(
            [
                "  - id: cautious-service-restart",
                "    name: Confirm service restarts",
                "    conditions:",
                "      - command: systemctl restart*",
                "        match_type: glob",
                "    effect: require_confirmation",
                "    message: Restarting services may cause brief interruptions",
                "    priority: 55",
                "",
                "  - id: cautious-package-install",
                "    name: Confirm package installations",
                "    conditions:",
                "      - command: apt install*",
                "        match_type: glob",
                "    effect: require_confirmation",
                "    message: Review packages before installation",
                "    priority: 55",
                "",
            ]
        )

    return "\n".join(lines)


class SetupWizard:
    """Interactive setup wizard for ELLE.

    Guides users through initial configuration with friendly,
    non-intimidating descriptions of each option.
    """

    def __init__(self, prompt: EllePrompt) -> None:
        """Initialize the wizard.

        Args:
            prompt: The prompt instance for user interaction.
        """
        self.prompt = prompt
        self.state = load_setup_state()
        self.prefs = self.state.preferences

    def run(self, reconfigure: bool = False) -> bool:
        """Run the setup wizard.

        Args:
            reconfigure: If True, running as reconfiguration (not first run).

        Returns:
            True if setup completed successfully, False if cancelled.
        """
        try:
            # Hide cursor during wizard for cleaner UI
            console.show_cursor(False)

            if reconfigure:
                self._show_reconfigure_intro()
            else:
                self._show_welcome()

            # Define steps (step function, can_go_back)
            steps = [
                (self._check_environment, False),  # Can't go back from first step
                (self._configure_safety, True),
                (self._configure_telemetry, True),
                (self._configure_features, True),
                (self._configure_privileges, True),
                (self._review_and_confirm, True),
            ]

            # Run steps with back navigation support
            current_step = 0
            while current_step < len(steps):
                step_func, can_go_back = steps[current_step]
                result = step_func(can_go_back=can_go_back)

                if result == StepResult.CONTINUE:
                    current_step += 1
                elif result == StepResult.BACK:
                    if current_step > 0:
                        current_step -= 1
                        console.print()
                        print_muted("Going back to previous step...")
                        console.print()
                elif result == StepResult.CANCEL:
                    console.show_cursor(True)
                    return False

            # Save configuration
            self._save_configuration()

            # Step 7: Start services (Ollama, models, daemon)
            if not self._start_services():
                # User skipped or services failed, but config is saved
                print_warning("Services not started. You can start them manually later.")

            self._show_completion()
            console.show_cursor(True)
            return True

        except (KeyboardInterrupt, EOFError):
            console.show_cursor(True)
            console.print()
            print_warning("Setup cancelled. You can run /reconfigure later.")
            return False

    def _show_welcome(self) -> None:
        """Display the welcome message."""
        console.print()
        welcome = Panel(
            Text.from_markup(
                "[bold]Welcome to ELLE[/bold]\n\n"
                "This quick setup will help you configure ELLE for your needs.\n"
                "You can change any of these settings later with [cyan]/reconfigure[/cyan].\n\n"
                "[dim]Press Ctrl+C at any time to skip setup.[/dim]"
            ),
            title="[bold cyan]First-Time Setup[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
        console.print(welcome)
        console.print()

    def _show_reconfigure_intro(self) -> None:
        """Display intro for reconfiguration."""
        console.print()
        console.print(section_header("ELLE Configuration", icon=Icons.SETTINGS))
        console.print()
        print_muted("Let's update your ELLE settings. Current values are shown as defaults.")
        console.print()

    def _check_environment(self, can_go_back: bool = False) -> StepResult:
        """Check the runtime environment and Ollama availability.

        Args:
            can_go_back: Whether to show back option (ignored for first step).

        Returns:
            StepResult indicating how to proceed.
        """
        console.print(section_rule("Step 1: Environment Check"))
        console.print()

        checks = []

        # Check Python version
        import sys

        py_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        checks.append(f"{Icons.SUCCESS} Python {py_version}")

        # Check for config directory
        if USER_CONFIG_DIR.exists():
            checks.append(f"{Icons.SUCCESS} Config directory exists")
        else:
            checks.append(f"{Icons.INFO} Config directory will be created")

        # Check Ollama
        ollama_status = self._check_ollama()
        if ollama_status["running"]:
            checks.append(f"{Icons.SUCCESS} Ollama is running")
            if ollama_status["models"]:
                model_list = ", ".join(ollama_status["models"][:3])
                if len(ollama_status["models"]) > 3:
                    model_list += f" (+{len(ollama_status['models']) - 3} more)"
                checks.append(f"  {Icons.BULLET} Models: {model_list}")
            self.prefs.ollama_verified = True
        elif ollama_status["installed"]:
            checks.append(f"{Icons.WARNING} Ollama is installed but not running")
            self.prefs.ollama_verified = False
        else:
            checks.append(f"{Icons.WARNING} Ollama is not installed")
            self.prefs.ollama_verified = False

        # Check telemetryd (C daemon) - REQUIRED for elled
        telemetryd_running = self._check_telemetryd_running()
        if telemetryd_running:
            checks.append(f"{Icons.SUCCESS} elled-telemetryd is running")
        else:
            checks.append(f"{Icons.WARNING} elled-telemetryd not running (required for elled)")

        # Check daemon (Python)
        daemon_running = self._check_daemon_running()
        if daemon_running:
            checks.append(f"{Icons.SUCCESS} elled (Python daemon) is running")
        else:
            checks.append(f"{Icons.WARNING} elled (Python daemon) is not running")

        for check in checks:
            console.print(f"  {check}")

        console.print()

        # Handle Ollama not running
        if not ollama_status["running"]:
            if ollama_status["installed"]:
                # Ollama is installed but not running - offer to start it
                print_warning("Ollama is installed but the server isn't running.")
                console.print()

                if self.prompt.prompt_confirm("Would you like to start Ollama now?", default=True):
                    console.print()
                    console.print(f"  {Icons.INFO} Starting Ollama...")
                    if self._start_ollama():
                        print_success("Ollama started successfully!")
                        self.prefs.ollama_verified = True
                        ollama_status["running"] = True
                    else:
                        print_warning("Failed to start Ollama automatically.")
                        console.print()
                        console.print(
                            Text.from_markup(
                                "[bold]To start Ollama manually:[/bold]\n"
                                "  [cyan]ollama serve[/cyan]  (run in a terminal)\n"
                                "  [dim]or[/dim]\n"
                                "  [cyan]systemctl --user start ollama[/cyan]  "
                                "(if installed as service)"
                            )
                        )
                else:
                    console.print()
                    print_muted("You can continue setup, but AI features won't work until Ollama is running.")
            else:
                # Ollama is not installed
                print_warning("ELLE requires Ollama for AI features. Install it from ollama.ai")
                console.print()
                print_muted("You can continue setup, but AI features won't work until Ollama is installed.")

            console.print()
            if not self.prompt.prompt_confirm("Continue setup without Ollama running?", default=True):
                return StepResult.CANCEL

        # Handle daemons not running (only ask if Ollama is running, otherwise defer to end)
        if (not daemon_running or not telemetryd_running) and ollama_status["running"]:
            console.print()
            print_muted("ELLE requires two daemons (start order matters):")
            print_muted("  1. elled-telemetryd: C daemon for telemetry collection (must start first)")
            print_muted("  2. elled: Python daemon for API and event processing")
            console.print()

            if self.prompt.prompt_confirm("Would you like to start the ELLE daemons now?", default=True):
                # Start telemetryd first
                if not telemetryd_running:
                    console.print()
                    console.print(f"  {Icons.INFO} Starting elled-telemetryd...")
                    telemetryd_result = self._start_telemetryd()
                    if telemetryd_result["success"]:
                        print_success(f"elled-telemetryd started ({telemetryd_result['method']})")
                    else:
                        print_muted(f"elled-telemetryd not available: {telemetryd_result['error']}")
                        print_muted("elled will use Python fallback watchers")

                # Then start elled
                if not daemon_running:
                    console.print()
                    console.print(f"  {Icons.INFO} Starting elled...")
                    daemon_result = self._start_daemon()
                    if daemon_result["success"]:
                        print_success(f"elled started ({daemon_result['method']})")
                    else:
                        print_warning(f"Failed to start elled: {daemon_result['error']}")
                        print_muted("The daemon will be started at the end of setup.")

        console.print()
        return StepResult.CONTINUE

    def _check_ollama(self) -> dict[str, Any]:
        """Check if Ollama is available and list models.

        Returns:
            Dict with keys:
                - installed: bool - whether ollama binary exists
                - running: bool - whether ollama server is responding
                - available: bool - alias for running (backwards compat)
                - models: list[str] - available model names
        """
        import shutil
        import subprocess

        result = {
            "installed": False,
            "running": False,
            "available": False,
            "models": [],
        }

        # Check if ollama binary is installed
        ollama_path = shutil.which("ollama")
        if ollama_path:
            result["installed"] = True
        else:
            # Check common installation paths
            common_paths: list[str | Path] = [
                "/usr/local/bin/ollama",
                "/usr/bin/ollama",
                Path.home() / ".local" / "bin" / "ollama",
            ]
            for path in common_paths:
                if Path(path).exists():
                    result["installed"] = True
                    break

        # If not found via path, try running ollama --version
        if not result["installed"]:
            try:
                subprocess.run(
                    ["ollama", "--version"],
                    capture_output=True,
                    timeout=2,
                    check=False,
                )
                result["installed"] = True
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        # Check if ollama server is running
        try:
            import httpx

            response = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
            if response.status_code == 200:
                data = response.json()
                result["models"] = [m["name"] for m in data.get("models", [])]
                result["running"] = True
                result["available"] = True
        except Exception as e:
            logger.debug(f"Ollama check failed: {e}")

        return result

    def _configure_safety(self, can_go_back: bool = True) -> StepResult:
        """Configure safety level and confirmation preferences.

        Args:
            can_go_back: Whether to show back option.

        Returns:
            StepResult indicating how to proceed.
        """
        console.print(section_rule("Step 2: Safety Settings"))
        if can_go_back:
            print_muted("(Press Esc to go back)")
        console.print()

        # Explain what this section is about
        console.print(
            Text.from_markup(
                "[bold]How should ELLE handle potentially risky operations?[/bold]\n\n"
                "ELLE can block dangerous commands and ask for confirmation before\n"
                "making changes. Choose the level that fits your comfort level."
            )
        )
        console.print()

        # Safety level selection - arrow key navigation
        options = []
        default_idx = 0
        for i, level in enumerate(SafetyLevel):
            info = SAFETY_LEVEL_INFO[level]
            options.append((level.value, info["name"], info["description"]))
            if level == self.prefs.safety_level:
                default_idx = i

        choice = self.prompt.prompt_select(
            "Safety Level:",
            options,
            default_index=default_idx,
        )

        if choice is None and can_go_back:
            return StepResult.BACK
        if choice:
            self.prefs.safety_level = SafetyLevel(choice)

        console.print()

        # Confirmation preference - arrow key navigation
        conf_options = []
        conf_default_idx = 0
        for i, pref in enumerate(ConfirmationPreference):
            info = CONFIRMATION_INFO[pref]
            conf_options.append((pref.value, info["name"], info["description"]))
            if pref == self.prefs.confirmation_preference:
                conf_default_idx = i

        conf_choice = self.prompt.prompt_select(
            "Confirmation Prompts:",
            conf_options,
            default_index=conf_default_idx,
        )

        if conf_choice is None and can_go_back:
            return StepResult.BACK
        if conf_choice:
            self.prefs.confirmation_preference = ConfirmationPreference(conf_choice)

        console.print()

        # Preview for config changes
        self.prefs.require_preview_for_configs = self.prompt.prompt_confirm(
            "Show preview before modifying config files?",
            default=self.prefs.require_preview_for_configs,
        )

        console.print()
        return StepResult.CONTINUE

    def _configure_telemetry(self, can_go_back: bool = True) -> StepResult:
        """Configure which telemetry sources to enable.

        Args:
            can_go_back: Whether to show back option.

        Returns:
            StepResult indicating how to proceed.
        """
        console.print(section_rule("Step 3: Telemetry Sources"))
        if can_go_back:
            print_muted("(Press Esc to go back)")
        console.print()

        # Build multi-select options
        telemetry_options: list[tuple[str, str, str, bool]] = [
            (
                "journal",
                str(TELEMETRY_INFO["journal"]["name"]),
                str(TELEMETRY_INFO["journal"]["description"]),
                self.prefs.journal_enabled,
            ),
            (
                "kernel",
                str(TELEMETRY_INFO["kernel"]["name"]),
                str(TELEMETRY_INFO["kernel"]["description"]),
                self.prefs.kernel_enabled,
            ),
            (
                "probes",
                str(TELEMETRY_INFO["probes"]["name"]),
                str(TELEMETRY_INFO["probes"]["description"]),
                self.prefs.probes_enabled,
            ),
            (
                "docker",
                str(TELEMETRY_INFO["docker"]["name"]),
                str(TELEMETRY_INFO["docker"]["description"]),
                self.prefs.docker_enabled,
            ),
            (
                "ebpf",
                str(TELEMETRY_INFO["ebpf"]["name"]) + " (advanced)",
                str(TELEMETRY_INFO["ebpf"]["description"]),
                self.prefs.ebpf_enabled,
            ),
        ]

        selected = self.prompt.prompt_multi_select(
            "What should ELLE monitor? (Space to toggle, Enter to confirm)",
            telemetry_options,
        )

        if selected is None and can_go_back:
            return StepResult.BACK

        if selected is not None:
            self.prefs.journal_enabled = "journal" in selected
            self.prefs.kernel_enabled = "kernel" in selected
            self.prefs.probes_enabled = "probes" in selected
            self.prefs.docker_enabled = "docker" in selected
            self.prefs.ebpf_enabled = "ebpf" in selected

        console.print()
        return StepResult.CONTINUE

    def _configure_features(self, can_go_back: bool = True) -> StepResult:
        """Configure optional features.

        Args:
            can_go_back: Whether to show back option.

        Returns:
            StepResult indicating how to proceed.
        """
        console.print(section_rule("Step 4: Optional Features"))
        if can_go_back:
            print_muted("(Press Esc to go back)")
        console.print()

        # Build multi-select options for features
        feature_options: list[tuple[str, str, str, bool]] = [
            (
                "api",
                str(FEATURE_INFO["api"]["name"]),
                str(FEATURE_INFO["api"]["description"]),
                self.prefs.api_enabled,
            ),
            (
                "gui_automation",
                str(FEATURE_INFO["gui_automation"]["name"]),
                str(FEATURE_INFO["gui_automation"]["description"]),
                self.prefs.gui_automation_enabled,
            ),
            (
                "auto_learn_packages",
                str(FEATURE_INFO["auto_learn_packages"]["name"]),
                str(FEATURE_INFO["auto_learn_packages"]["description"]),
                self.prefs.auto_learn_packages,
            ),
        ]

        selected = self.prompt.prompt_multi_select(
            "Which features should be enabled? (Space to toggle, Enter to confirm)",
            feature_options,
        )

        if selected is None and can_go_back:
            return StepResult.BACK

        if selected is not None:
            self.prefs.api_enabled = "api" in selected
            self.prefs.gui_automation_enabled = "gui_automation" in selected
            self.prefs.auto_learn_packages = "auto_learn_packages" in selected

        console.print()
        return StepResult.CONTINUE

    def _configure_privileges(self, can_go_back: bool = True) -> StepResult:
        """Configure Polkit privilege level.

        Args:
            can_go_back: Whether to show back option.

        Returns:
            StepResult indicating how to proceed.
        """
        console.print(section_rule("Step 5: Privilege Configuration"))
        if can_go_back:
            print_muted("(Press Esc to go back)")
        console.print()

        console.print(
            Text.from_markup(
                "[bold]How should ELLE handle privileged operations?[/bold]\n\n"
                "Many ELLE features require root access (editing /etc files,\n"
                "managing services, etc.). By default, you'll be prompted for\n"
                "your password. You can configure this to be more convenient."
            )
        )
        console.print()

        # Privilege level selection - arrow key navigation
        priv_options = []
        priv_default_idx = 0
        for i, level in enumerate(PrivilegeLevel):
            info = PRIVILEGE_LEVEL_INFO[level]
            desc = info["description"]
            if "warning" in info:
                desc += f" {Icons.WARNING} {info['warning']}"
            priv_options.append((level.value, info["name"], desc))
            if level == self.prefs.privilege_level:
                priv_default_idx = i

        priv_choice = self.prompt.prompt_select(
            "Privilege Level:",
            priv_options,
            default_index=priv_default_idx,
        )

        if priv_choice is None and can_go_back:
            return StepResult.BACK
        if priv_choice:
            self.prefs.privilege_level = PrivilegeLevel(priv_choice)

        console.print()

        # If convenient or passwordless, explain what will happen
        if self.prefs.privilege_level == PrivilegeLevel.CONVENIENT:
            console.print(
                Text.from_markup(
                    "[bold]Group-based authentication selected.[/bold]\n\n"
                    "During setup completion, ELLE will:\n"
                    "  1. Create a system group called 'elle'\n"
                    "  2. Add your user to the 'elle' group\n"
                    "  3. Install a Polkit rule granting 'elle' group members\n"
                    "     passwordless access to ELLE operations\n\n"
                    "[dim]You'll need to log out and back in for group membership\n"
                    "to take effect.[/dim]"
                )
            )
            console.print()

            if not self.prompt.prompt_confirm("Proceed with group-based configuration?", default=True):
                self.prefs.privilege_level = PrivilegeLevel.SECURE
                console.print()
                print_muted("Reverted to secure (password) mode.")
                console.print()

        elif self.prefs.privilege_level == PrivilegeLevel.PASSWORDLESS:
            console.print()
            print_warning(
                "Passwordless mode allows ANY process running as your user to "
                "perform privileged system operations without authentication."
            )
            console.print()
            console.print(
                Text.from_markup(
                    "[bold]This is a significant security risk.[/bold]\n"
                    "Only use this on:\n"
                    "  - Single-user development machines\n"
                    "  - Virtual machines for testing\n"
                    "  - Systems where convenience outweighs security\n"
                )
            )
            console.print()

            if not self.prompt.prompt_confirm("I understand the risks. Enable passwordless mode?", default=False):
                self.prefs.privilege_level = PrivilegeLevel.SECURE
                console.print()
                print_muted("Reverted to secure (password) mode.")
                console.print()

        return StepResult.CONTINUE

    def _review_and_confirm(self, can_go_back: bool = True) -> StepResult:
        """Show summary and confirm settings.

        Args:
            can_go_back: Whether to allow going back.

        Returns:
            StepResult indicating how to proceed.
        """
        console.print(section_rule("Step 6: Review Settings"))
        if can_go_back:
            print_muted("(Enter 'b' or 'back' to go back)")
        console.print()

        # Build summary
        safety_info = SAFETY_LEVEL_INFO[self.prefs.safety_level]
        conf_info = CONFIRMATION_INFO[self.prefs.confirmation_preference]

        summary_items = [
            f"Safety: {safety_info['name']}",
            f"Confirmations: {conf_info['name']}",
            f"Config preview: {'Yes' if self.prefs.require_preview_for_configs else 'No'}",
        ]

        telemetry_enabled = []
        if self.prefs.journal_enabled:
            telemetry_enabled.append("Journal")
        if self.prefs.kernel_enabled:
            telemetry_enabled.append("Kernel")
        if self.prefs.probes_enabled:
            telemetry_enabled.append("Probes")
        if self.prefs.docker_enabled:
            telemetry_enabled.append("Docker")
        if self.prefs.ebpf_enabled:
            telemetry_enabled.append("eBPF")

        summary_items.append(f"Telemetry: {', '.join(telemetry_enabled) or 'None'}")

        features_enabled = []
        if self.prefs.api_enabled:
            features_enabled.append("API")
        if self.prefs.gui_automation_enabled:
            features_enabled.append("GUI")
        if self.prefs.auto_learn_packages:
            features_enabled.append("Auto-Learn")

        summary_items.append(f"Features: {', '.join(features_enabled) or 'None'}")

        # Privilege level
        priv_info = PRIVILEGE_LEVEL_INFO[self.prefs.privilege_level]
        summary_items.append(f"Privileges: {priv_info['name']}")

        summary = Panel(
            bulleted_list(summary_items),
            title="[bold]Your Configuration[/bold]",
            border_style="cyan",
            padding=(1, 2),
        )
        console.print(summary)
        console.print()

        # Use a custom prompt that accepts back
        response = self.prompt.prompt_confirm_with_back(
            "Save these settings?",
            default=True,
            allow_back=can_go_back,
        )

        if response == "back":
            return StepResult.BACK
        elif response:
            return StepResult.CONTINUE
        else:
            return StepResult.CANCEL

    def _save_configuration(self) -> None:
        """Save the configuration to files."""
        # Update state
        self.state.completed = True
        self.state.completed_at = datetime.now()
        self.state.preferences = self.prefs

        # Save to config file
        save_setup_state(self.state)

        # Generate policy file
        generate_policy_file(self.prefs)

        # Configure Polkit privileges if non-default
        if self.prefs.privilege_level != PrivilegeLevel.SECURE:
            console.print()
            print_muted("Configuring Polkit privileges (requires sudo)...")
            success, message = configure_polkit_privileges(self.prefs)
            if success:
                self.prefs.polkit_configured = True
                print_success(message)
            else:
                print_warning(f"Polkit configuration failed: {message}")
                print_muted("You can retry later with: sudo elle configure-polkit")
            # Re-save state with polkit_configured flag
            self.state.preferences = self.prefs
            save_setup_state(self.state)

    def _start_services(self) -> bool:
        """Start Ollama, pull models, and start the daemons.

        Returns:
            True if all services started successfully.
        """
        console.print(section_rule("Starting Services"))
        console.print()

        console.print(
            Text.from_markup(
                "[bold]ELLE needs these services running to work properly:[/bold]\n"
                "  1. Ollama (local AI inference server)\n"
                "  2. Language models (SLM for classification, LLM for generation)\n"
                "  3. elled-telemetryd (C telemetry daemon - collects system events)\n"
                "  4. elled (Python daemon - API and event processing)\n"
            )
        )
        console.print()

        if not self.prompt.prompt_confirm("Start these services now?", default=True):
            return False

        console.print()
        success = True

        # Step 1: Start Ollama if needed
        ollama_status = self._check_ollama()
        if not ollama_status["running"]:
            if ollama_status["installed"]:
                console.print(f"  {Icons.INFO} Starting Ollama...")
                if self._start_ollama():
                    print_success("Ollama started")
                    ollama_status["running"] = True
                    self.prefs.ollama_verified = True
                else:
                    print_warning("Failed to start Ollama. You may need to start it manually.")
                    success = False
            else:
                print_warning("Ollama is not installed. Install from https://ollama.ai")
                success = False
        else:
            console.print(f"  {Icons.SUCCESS} Ollama is already running")
            self.prefs.ollama_verified = True

        console.print()

        # Step 2: Pull and warm models (only if Ollama is running)
        if ollama_status.get("running"):
            if not self._setup_models():
                print_warning("Model setup incomplete. Some AI features may be unavailable.")
                success = False
        else:
            print_warning("Skipping model setup - Ollama is not running")

        console.print()

        # Step 3: Start elled-telemetryd (C daemon for telemetry collection)
        # This is REQUIRED - elled depends on telemetryd for event collection
        telemetryd_running = self._check_telemetryd_running()
        if not telemetryd_running:
            console.print(f"  {Icons.INFO} Starting elled-telemetryd (C telemetry daemon)...")
            telemetryd_result = self._start_telemetryd()
            if telemetryd_result["success"]:
                print_success(f"elled-telemetryd started ({telemetryd_result['method']})")
                telemetryd_running = True
            else:
                print_warning(f"Failed to start elled-telemetryd: {telemetryd_result['error']}")
                print_muted("elled requires telemetryd for event collection")
                success = False
        else:
            console.print(f"  {Icons.SUCCESS} elled-telemetryd is already running")

        console.print()

        # Step 4: Start elled (Python daemon)
        console.print(f"  {Icons.INFO} Starting elled (Python daemon)...")
        daemon_result = self._start_daemon()
        if daemon_result["success"]:
            print_success(f"elled started ({daemon_result['method']})")
        else:
            print_warning(f"Failed to start elled: {daemon_result['error']}")
            console.print()
            print_muted("You can start it manually with: sudo systemctl start elled")
            success = False

        console.print()
        return success

    def _start_ollama(self) -> bool:
        """Start the Ollama service.

        Returns:
            True if Ollama started successfully.
        """
        # Try systemctl first (user service)
        try:
            result = subprocess.run(
                ["systemctl", "--user", "start", "ollama"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                # Wait for it to be ready
                return self._wait_for_ollama(timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Try systemctl (system service)
        try:
            result = subprocess.run(
                ["systemctl", "start", "ollama"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return self._wait_for_ollama(timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Try running ollama serve in background
        ollama_path = shutil.which("ollama")
        if not ollama_path:
            for path in ["/usr/local/bin/ollama", "/usr/bin/ollama"]:
                if Path(path).exists():
                    ollama_path = path
                    break

        if ollama_path:
            try:
                # Start in background (nohup-style)
                subprocess.Popen(
                    [ollama_path, "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return self._wait_for_ollama(timeout=30)
            except Exception as e:
                logger.debug(f"Failed to start Ollama: {e}")

        return False

    def _wait_for_ollama(self, timeout: int = 30) -> bool:
        """Wait for Ollama to become available.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            True if Ollama is responding.
        """
        import httpx

        start = time.time()
        while time.time() - start < timeout:
            try:
                response = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
                if response.status_code == 200:
                    return True
            except httpx.RequestError:
                pass
            time.sleep(1)
        return False

    def _setup_models(self) -> bool:
        """Pull and warm up required LLM models.

        Returns:
            True if models are ready.
        """
        from elle.rag.constants import LLM_MODEL, SLM_MODEL

        console.print(f"  {Icons.INFO} Setting up language models...")
        console.print()

        # Run async setup in sync context
        try:
            return asyncio.get_event_loop().run_until_complete(self._setup_models_async(SLM_MODEL, LLM_MODEL))
        except RuntimeError:
            # No event loop running, create one
            return asyncio.run(self._setup_models_async(SLM_MODEL, LLM_MODEL))

    async def _setup_models_async(self, slm_model: str, llm_model: str) -> bool:
        """Async implementation of model setup.

        Args:
            slm_model: SLM model name.
            llm_model: LLM model name.

        Returns:
            True if models are ready.
        """
        from elle.rag.model_warmup import ModelWarmupService

        warmup = ModelWarmupService()

        try:
            # Check which models are already available
            available = await warmup.list_models()
            slm_exists = any(slm_model in m for m in available)
            llm_exists = any(llm_model in m for m in available)

            # Pull models with progress
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
            ) as progress:
                # Pull SLM if needed
                if not slm_exists:
                    task = progress.add_task(f"Pulling {slm_model}...", total=None)
                    success = await self._pull_model_with_progress(slm_model, progress, task)
                    if success:
                        desc = f"[green]{Icons.SUCCESS}[/green] {slm_model}"
                        progress.update(task, description=desc)
                    else:
                        desc = f"[red]{Icons.ERROR}[/red] Failed: {slm_model}"
                        progress.update(task, description=desc)
                        return False
                else:
                    console.print(f"    {Icons.SUCCESS} {slm_model} already available")

                # Pull LLM if needed
                if not llm_exists:
                    task = progress.add_task(f"Pulling {llm_model}...", total=None)
                    success = await self._pull_model_with_progress(llm_model, progress, task)
                    if success:
                        desc = f"[green]{Icons.SUCCESS}[/green] {llm_model}"
                        progress.update(task, description=desc)
                    else:
                        desc = f"[red]{Icons.ERROR}[/red] Failed: {llm_model}"
                        progress.update(task, description=desc)
                        return False
                else:
                    console.print(f"    {Icons.SUCCESS} {llm_model} already available")

            console.print()

            # Warm up models with progress spinner
            console.print(f"    {Icons.INFO} Warming up models (first load can take 1-2 minutes)...")
            console.print("    [dim]Press Ctrl+C to skip warmup - models will load on first use[/dim]")
            console.print()

            warmup_skipped = False
            try:
                # Warm SLM with spinner
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                    transient=True,
                ) as progress:
                    slm_task = progress.add_task(f"    Loading {slm_model}...", total=None)

                    # Run warmup with timeout handling
                    try:
                        slm_result = await asyncio.wait_for(warmup.warm_slm(), timeout=300.0)
                    except TimeoutError:
                        from elle.rag.model_warmup import WarmupResult
                        slm_result = WarmupResult(
                            success=False,
                            model=slm_model,
                            message="Warmup timed out after 5 minutes",
                            error="timeout",
                        )

                    progress.remove_task(slm_task)

                if slm_result.success:
                    console.print(f"    {Icons.SUCCESS} SLM ready ({slm_result.duration_ms:.0f}ms)")
                else:
                    console.print(f"    {Icons.WARNING} SLM warmup failed: {slm_result.error}")
                    console.print(f"    {Icons.INFO} SLM will load on first use (may be slow)")

                # Warm LLM if sufficient VRAM
                if warmup.has_sufficient_vram():
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        console=console,
                        transient=True,
                    ) as progress:
                        llm_task = progress.add_task(f"    Loading {llm_model}...", total=None)

                        try:
                            llm_result = await asyncio.wait_for(warmup.warm_llm(), timeout=300.0)
                        except TimeoutError:
                            from elle.rag.model_warmup import WarmupResult
                            llm_result = WarmupResult(
                                success=False,
                                model=llm_model,
                                message="Warmup timed out after 5 minutes",
                                error="timeout",
                            )

                        progress.remove_task(llm_task)

                    if llm_result.success:
                        console.print(f"    {Icons.SUCCESS} LLM ready ({llm_result.duration_ms:.0f}ms)")
                    else:
                        console.print(f"    {Icons.WARNING} LLM warmup failed: {llm_result.error}")
                        console.print(f"    {Icons.INFO} LLM will load on first use (may be slow)")
                else:
                    console.print(f"    {Icons.INFO} LLM will load on-demand (limited VRAM)")

            except (KeyboardInterrupt, asyncio.CancelledError):
                warmup_skipped = True
                console.print()
                console.print(f"    {Icons.INFO} Warmup skipped - models will load on first use")

            if warmup_skipped:
                console.print(f"    {Icons.INFO} This may cause a delay on your first query")

            return True

        except Exception as e:
            console.print(f"    {Icons.ERROR} Model setup error: {e}")
            return False
        finally:
            await warmup.close()

    async def _pull_model_with_progress(
        self,
        model: str,
        progress: Progress,
        task_id: TaskID,
    ) -> bool:
        """Pull a model with progress updates.

        Args:
            model: Model name to pull.
            progress: Rich Progress instance.
            task_id: Progress task ID.

        Returns:
            True if pull succeeded.
        """
        import json

        import httpx

        try:
            client = httpx.AsyncClient(timeout=httpx.Timeout(600.0))
            async with (
                client,
                client.stream(
                    "POST",
                    "http://localhost:11434/api/pull",
                    json={"name": model, "stream": True},
                ) as response,
            ):
                if response.status_code != 200:
                    return False

                async for line in response.aiter_lines():
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Update progress based on response
                    status = data.get("status", "")
                    if "total" in data and "completed" in data:
                        progress.update(
                            task_id,
                            total=data["total"],
                            completed=data["completed"],
                            description=f"Pulling {model}: {status}",
                        )
                    elif status:
                        desc = f"Pulling {model}: {status}"
                        progress.update(task_id, description=desc)

                    # Check for completion
                    if data.get("status") == "success":
                        total = progress.tasks[task_id].total or 100
                        progress.update(task_id, completed=total)
                        return True

                return True

        except Exception:
            return False

    def _start_telemetryd(self) -> dict[str, Any]:
        """Start the elled-telemetryd C daemon.

        Returns:
            Dict with keys:
                - success: bool
                - method: str (how it was started)
                - error: str | None
        """
        # Try systemctl first (if installed as system service)
        try:
            result = subprocess.run(
                ["systemctl", "status", "elled-telemetryd"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            # Service exists
            if result.returncode in (0, 3):  # 0=running, 3=stopped
                if result.returncode == 0:
                    # Already running
                    return {"success": True, "method": "systemd (already running)", "error": None}

                # Try to start it
                start_result = subprocess.run(
                    ["sudo", "systemctl", "start", "elled-telemetryd"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if start_result.returncode == 0:
                    # Wait for socket to be ready
                    time.sleep(1)
                    if self._check_telemetryd_running():
                        return {"success": True, "method": "systemd", "error": None}
                    else:
                        return {"success": False, "method": "systemd", "error": "Started but socket not ready"}
                else:
                    return {
                        "success": False,
                        "method": "systemd",
                        "error": start_result.stderr.strip() or "Failed to start",
                    }
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Try running elled-telemetryd directly
        telemetryd_path = shutil.which("elled-telemetryd")
        if telemetryd_path:
            try:
                subprocess.Popen(
                    [telemetryd_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                time.sleep(2)
                if self._check_telemetryd_running():
                    return {"success": True, "method": "elled-telemetryd binary", "error": None}
            except Exception as e:
                return {"success": False, "method": "elled-telemetryd binary", "error": str(e)}

        return {"success": False, "method": "none", "error": "elled-telemetryd not installed"}

    def _start_daemon(self) -> dict[str, Any]:
        """Start the ELLE daemon (elled).

        Returns:
            Dict with keys:
                - success: bool
                - method: str (how it was started)
                - error: str | None
        """
        # Try systemctl first (if installed as system service)
        try:
            result = subprocess.run(
                ["systemctl", "status", "elled"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            # Service exists
            if result.returncode in (0, 3):  # 0=running, 3=stopped
                if result.returncode == 0:
                    # Already running
                    return {"success": True, "method": "systemd (already running)", "error": None}

                # Try to start it
                start_result = subprocess.run(
                    ["sudo", "systemctl", "start", "elled"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if start_result.returncode == 0:
                    return {"success": True, "method": "systemd", "error": None}
                else:
                    return {
                        "success": False,
                        "method": "systemd",
                        "error": start_result.stderr.strip() or "Failed to start",
                    }
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Try user systemd service
        try:
            result = subprocess.run(
                ["systemctl", "--user", "status", "elled"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode in (0, 3):
                if result.returncode == 0:
                    return {
                        "success": True,
                        "method": "systemd --user (already running)",
                        "error": None,
                    }

                start_result = subprocess.run(
                    ["systemctl", "--user", "start", "elled"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if start_result.returncode == 0:
                    return {
                        "success": True,
                        "method": "systemd --user",
                        "error": None,
                    }
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Try running elled directly in background
        elled_path = shutil.which("elled")
        if not elled_path:
            # Check if we can run it via python module
            try:
                subprocess.Popen(
                    ["python3", "-m", "elle.daemon.main"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                # Wait a moment and check if API is responding
                time.sleep(2)
                if self._check_daemon_running():
                    return {"success": True, "method": "python module", "error": None}
            except Exception as e:
                return {"success": False, "method": "python module", "error": str(e)}

        if elled_path:
            try:
                subprocess.Popen(
                    [elled_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                time.sleep(2)
                if self._check_daemon_running():
                    return {"success": True, "method": "elled binary", "error": None}
            except Exception as e:
                return {"success": False, "method": "elled binary", "error": str(e)}

        return {"success": False, "method": "none", "error": "No method available to start daemon"}

    def _check_daemon_running(self) -> bool:
        """Check if the daemon API is responding.

        Returns:
            True if daemon is running.
        """
        import httpx

        try:
            response = httpx.get("http://localhost:8377/health", timeout=2.0)
            return response.status_code == 200
        except httpx.RequestError:
            return False

    def _check_telemetryd_running(self) -> bool:
        """Check if the elled-telemetryd C daemon is running.

        Returns:
            True if telemetryd socket exists and is connectable.
        """
        import socket
        from pathlib import Path

        socket_path = Path("/run/elle/telemetry.sock")
        if not socket_path.exists():
            return False

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(str(socket_path))
            sock.close()
            return True
        except OSError:
            return False

    def _show_completion(self) -> None:
        """Show completion message."""
        console.print()
        print_success("Setup complete!")
        console.print()

        # Check what's actually running now
        ollama_status = self._check_ollama()
        daemon_running = self._check_daemon_running()
        telemetryd_running = self._check_telemetryd_running()

        if ollama_status["running"] and daemon_running and telemetryd_running:
            console.print(Text.from_markup("[bold green]ELLE is ready![/bold green] All services are running.\n"))
        else:
            issues = []
            if not ollama_status["running"]:
                issues.append("Ollama is not running")
            if not telemetryd_running:
                issues.append("elled-telemetryd not running")
            if not daemon_running:
                issues.append("elled is not running")
            console.print(Text.from_markup(f"[bold yellow]Partial setup:[/bold yellow] {', '.join(issues)}\n"))

        next_steps = [
            "Type a question to get started (e.g., 'how much disk space is left?')",
            "Use /help to see available commands",
            "Run /reconfigure to change these settings later",
        ]

        console.print("[bold]Next steps:[/bold]")
        console.print(numbered_list(next_steps))
        console.print()

        if not ollama_status["running"]:
            if ollama_status["installed"]:
                console.print(tip("Start Ollama with: ollama serve"))
            else:
                console.print(tip("Install Ollama from ollama.ai to enable AI features"))
            console.print()

        if not daemon_running:
            console.print(tip("Start the daemons with: sudo systemctl start elled-telemetryd elled"))
            console.print()

        if self.prefs.privilege_level == PrivilegeLevel.CONVENIENT and self.prefs.polkit_configured:
            console.print(tip("Log out and back in for group-based authentication to take effect"))
            console.print()


def run_setup_wizard(prompt: EllePrompt, reconfigure: bool = False) -> bool:
    """Run the setup wizard.

    Args:
        prompt: The prompt instance for user interaction.
        reconfigure: If True, running as reconfiguration.

    Returns:
        True if setup completed successfully.
    """
    wizard = SetupWizard(prompt)
    return wizard.run(reconfigure=reconfigure)
