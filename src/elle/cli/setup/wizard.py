"""ELLE Setup Wizard.

A friendly, interactive wizard that guides users through initial configuration.
Runs on first launch and can be re-run via /reconfigure or /policies.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.panel import Panel
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
    except Exception:
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
    except Exception:
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
    config: dict = {}
    if USER_CONFIG_FILE.exists():
        try:
            import tomllib

            with open(USER_CONFIG_FILE, "rb") as f:
                config = tomllib.load(f)
        except Exception:
            pass

    # Update setup section
    config["setup"] = {
        "completed": state.completed,
        "completed_at": state.completed_at.isoformat() if state.completed_at else None,
        "version": state.version,
        "preferences": state.preferences.model_dump(),
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

    # Set default effect based on safety level
    if prefs.safety_level == SafetyLevel.CAUTIOUS:
        lines.append("default_effect: REQUIRE_CONFIRMATION")
    elif prefs.safety_level == SafetyLevel.MINIMAL:
        lines.append("default_effect: ALLOW")
    else:
        lines.append("default_effect: ALLOW")

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
                "    effect: REQUIRE_CONFIRMATION",
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
                "    effect: ALLOW",
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
                "    effect: REQUIRE_PREVIEW",
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
                "    effect: REQUIRE_CONFIRMATION",
                "    message: Restarting services may cause brief interruptions",
                "    priority: 55",
                "",
                "  - id: cautious-package-install",
                "    name: Confirm package installations",
                "    conditions:",
                "      - command: apt install*",
                "        match_type: glob",
                "    effect: REQUIRE_CONFIRMATION",
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
            if reconfigure:
                self._show_reconfigure_intro()
            else:
                self._show_welcome()

            # Step 1: Check environment
            if not self._check_environment():
                return False

            # Step 2: Safety and confirmation preferences
            if not self._configure_safety():
                return False

            # Step 3: Telemetry sources
            if not self._configure_telemetry():
                return False

            # Step 4: Optional features
            if not self._configure_features():
                return False

            # Step 5: Privilege configuration
            if not self._configure_privileges():
                return False

            # Step 6: Review and confirm
            if not self._review_and_confirm():
                return False

            # Save configuration
            self._save_configuration()

            self._show_completion()
            return True

        except (KeyboardInterrupt, EOFError):
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

    def _check_environment(self) -> bool:
        """Check the runtime environment and Ollama availability."""
        console.print(section_rule("Environment Check"))
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

        for check in checks:
            console.print(f"  {check}")

        console.print()

        if not ollama_status["running"]:
            if ollama_status["installed"]:
                # Ollama is installed but not running
                print_warning("Ollama is installed but the server isn't running.")
                console.print()
                console.print(
                    Text.from_markup(
                        "[bold]To start Ollama:[/bold]\n"
                        "  [cyan]ollama serve[/cyan]  (run in a terminal)\n"
                        "  [dim]or[/dim]\n"
                        "  [cyan]systemctl --user start ollama[/cyan]  (if installed as service)"
                    )
                )
            else:
                # Ollama is not installed
                print_warning("ELLE requires Ollama for AI features. Install it from ollama.ai")
            console.print()
            print_muted(
                "You can continue setup, but AI features won't work until Ollama is running."
            )
            console.print()

            if not self.prompt.prompt_confirm("Continue setup without Ollama?", default=True):
                return False

        return True

    def _check_ollama(self) -> dict:
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
            common_paths = [
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
        except Exception:
            pass

        return result

    def _configure_safety(self) -> bool:
        """Configure safety level and confirmation preferences."""
        console.print(section_rule("Safety Settings"))
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

        if conf_choice:
            self.prefs.confirmation_preference = ConfirmationPreference(conf_choice)

        console.print()

        # Preview for config changes
        self.prefs.require_preview_for_configs = self.prompt.prompt_confirm(
            "Show preview before modifying config files?",
            default=self.prefs.require_preview_for_configs,
        )

        console.print()
        return True

    def _configure_telemetry(self) -> bool:
        """Configure which telemetry sources to enable."""
        console.print(section_rule("Telemetry Sources"))
        console.print()

        # Build multi-select options
        telemetry_options = [
            (
                "journal",
                TELEMETRY_INFO["journal"]["name"],
                TELEMETRY_INFO["journal"]["description"],
                self.prefs.journal_enabled,
            ),
            (
                "kernel",
                TELEMETRY_INFO["kernel"]["name"],
                TELEMETRY_INFO["kernel"]["description"],
                self.prefs.kernel_enabled,
            ),
            (
                "probes",
                TELEMETRY_INFO["probes"]["name"],
                TELEMETRY_INFO["probes"]["description"],
                self.prefs.probes_enabled,
            ),
            (
                "docker",
                TELEMETRY_INFO["docker"]["name"],
                TELEMETRY_INFO["docker"]["description"],
                self.prefs.docker_enabled,
            ),
            (
                "ebpf",
                TELEMETRY_INFO["ebpf"]["name"] + " (advanced)",
                TELEMETRY_INFO["ebpf"]["description"],
                self.prefs.ebpf_enabled,
            ),
        ]

        selected = self.prompt.prompt_multi_select(
            "What should ELLE monitor? (Space to toggle, Enter to confirm)",
            telemetry_options,
        )

        if selected is not None:
            self.prefs.journal_enabled = "journal" in selected
            self.prefs.kernel_enabled = "kernel" in selected
            self.prefs.probes_enabled = "probes" in selected
            self.prefs.docker_enabled = "docker" in selected
            self.prefs.ebpf_enabled = "ebpf" in selected

        console.print()
        return True

    def _configure_features(self) -> bool:
        """Configure optional features."""
        console.print(section_rule("Optional Features"))
        console.print()

        # Build multi-select options for features
        feature_options = [
            (
                "api",
                FEATURE_INFO["api"]["name"],
                FEATURE_INFO["api"]["description"],
                self.prefs.api_enabled,
            ),
            (
                "gui_automation",
                FEATURE_INFO["gui_automation"]["name"],
                FEATURE_INFO["gui_automation"]["description"],
                self.prefs.gui_automation_enabled,
            ),
            (
                "auto_learn_packages",
                FEATURE_INFO["auto_learn_packages"]["name"],
                FEATURE_INFO["auto_learn_packages"]["description"],
                self.prefs.auto_learn_packages,
            ),
        ]

        selected = self.prompt.prompt_multi_select(
            "Which features should be enabled? (Space to toggle, Enter to confirm)",
            feature_options,
        )

        if selected is not None:
            self.prefs.api_enabled = "api" in selected
            self.prefs.gui_automation_enabled = "gui_automation" in selected
            self.prefs.auto_learn_packages = "auto_learn_packages" in selected

        console.print()
        return True

    def _configure_privileges(self) -> bool:
        """Configure Polkit privilege level."""
        console.print(section_rule("Privilege Configuration"))
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

            if not self.prompt.prompt_confirm(
                "Proceed with group-based configuration?", default=True
            ):
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

            if not self.prompt.prompt_confirm(
                "I understand the risks. Enable passwordless mode?", default=False
            ):
                self.prefs.privilege_level = PrivilegeLevel.SECURE
                console.print()
                print_muted("Reverted to secure (password) mode.")
                console.print()

        return True

    def _review_and_confirm(self) -> bool:
        """Show summary and confirm settings."""
        console.print(section_rule("Review Settings"))
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

        return self.prompt.prompt_confirm("Save these settings?", default=True)

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

    def _show_completion(self) -> None:
        """Show completion message."""
        console.print()
        print_success("Setup complete!")
        console.print()

        next_steps = [
            "Type a question to get started (e.g., 'how much disk space is left?')",
            "Use /help to see available commands",
            "Run /reconfigure to change these settings later",
        ]

        console.print("[bold]Next steps:[/bold]")
        console.print(numbered_list(next_steps))
        console.print()

        if not self.prefs.ollama_verified:
            # Re-check ollama status for accurate tip
            ollama_status = self._check_ollama()
            if ollama_status["running"]:
                # User started it during setup
                console.print(tip("Ollama is now running. You're ready to go!"))
            elif ollama_status["installed"]:
                console.print(tip("Start Ollama with: ollama serve"))
            else:
                console.print(tip("Install Ollama from ollama.ai to enable AI features"))
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
