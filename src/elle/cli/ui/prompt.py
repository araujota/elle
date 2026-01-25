"""Enhanced Prompt for ELLE REPL.

Provides a delightful prompt with status indicators and optional status bar.
Uses prompt_toolkit for advanced input handling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion, WordCompleter
from prompt_toolkit.formatted_text import HTML, FormattedText, merge_formatted_text
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.lexers import PygmentsLexer
from prompt_toolkit.styles import Style

from elle.cli.ui.theme import Colors, Icons

if TYPE_CHECKING:
    from pathlib import Path
    from prompt_toolkit.document import Document


# ELLE prompt style
PROMPT_STYLE = Style.from_dict({
    # Prompt components
    "prompt": "ansicyan bold",
    "prompt.success": "ansigreen",
    "prompt.warning": "ansiyellow",
    "prompt.error": "ansired",
    "prompt.chevron": "ansicyan",

    # Status bar
    "status": "bg:ansibrightblack ansiwhite",
    "status.key": "ansicyan",
    "status.value": "ansiwhite",
    "status.separator": "ansibrightblack",
    "status.warning": "ansiyellow",
    "status.error": "ansired",

    # Input
    "": "",  # Default
    "command": "ansiwhite bold",

    # Completion menu
    "completion-menu": "bg:ansibrightblack ansiwhite",
    "completion-menu.completion": "",
    "completion-menu.completion.current": "bg:ansicyan ansiblack",
    "scrollbar.background": "bg:ansibrightblack",
    "scrollbar.button": "bg:ansicyan",
})


# Slash commands for completion
SLASH_COMMANDS = [
    ("/help", "Show help"),
    ("/status", "Show system status"),
    ("/events", "Show recent events"),
    ("/incidents", "Show open incidents"),
    ("/man", "Search Man Vault"),
    ("/fix", "Diagnose last failure"),
    ("/apply", "Apply pending changes"),
    ("/history", "Show command history"),
    ("/clear", "Clear screen"),
    ("/exit", "Exit ELLE"),
]


class ElleCompleter(Completer):
    """Custom completer for ELLE slash commands and history."""

    def __init__(self) -> None:
        self.slash_completer = WordCompleter(
            [cmd for cmd, _ in SLASH_COMMANDS],
            meta_dict={cmd: desc for cmd, desc in SLASH_COMMANDS},
            sentence=True,
        )

    def get_completions(
        self,
        document: "Document",
        complete_event: object,
    ):
        """Generate completions for current input."""
        text = document.text_before_cursor

        # Slash commands
        if text.startswith("/"):
            yield from self.slash_completer.get_completions(document, complete_event)
            return

        # Could add more completions here:
        # - Common shell commands
        # - File paths
        # - Incident IDs


def get_prompt_text(
    *,
    state: Literal["idle", "success", "warning", "error"] = "idle",
    prefix: str = "elle",
) -> FormattedText:
    """Generate the prompt text with state indicator.

    Args:
        state: Current prompt state
        prefix: Prompt prefix text

    Returns:
        FormattedText for prompt_toolkit
    """
    # State indicator
    indicator_map = {
        "idle": (Icons.PROMPT_IDLE, "class:prompt"),
        "success": (Icons.PROMPT_SUCCESS, "class:prompt.success"),
        "warning": (Icons.PROMPT_WARNING, "class:prompt.warning"),
        "error": (Icons.PROMPT_ERROR, "class:prompt.error"),
    }

    icon, style = indicator_map.get(state, indicator_map["idle"])

    return FormattedText([
        ("class:prompt", f"{prefix} "),
        (style, f"{icon} "),
    ])


def get_status_bar(
    *,
    model: str | None = None,
    daemon_status: str | None = None,
    vault_docs: int | None = None,
    incidents_open: int = 0,
    cwd: str | None = None,
) -> FormattedText:
    """Generate the status bar content.

    Args:
        model: Current LLM model name
        daemon_status: "running", "stopped", etc.
        vault_docs: Number of indexed documents
        incidents_open: Number of open incidents
        cwd: Current working directory

    Returns:
        FormattedText for the status bar
    """
    parts: list[tuple[str, str]] = []

    # Model
    if model:
        parts.append(("class:status.key", "Model: "))
        parts.append(("class:status.value", model))

    # Daemon
    if daemon_status:
        if parts:
            parts.append(("class:status.separator", " │ "))

        parts.append(("class:status.key", "Daemon: "))
        if daemon_status == "running":
            parts.append(("class:status.value", daemon_status))
        else:
            parts.append(("class:status.warning", daemon_status))

    # Vault
    if vault_docs is not None:
        if parts:
            parts.append(("class:status.separator", " │ "))

        parts.append(("class:status.key", "Vault: "))
        parts.append(("class:status.value", f"{vault_docs:,}"))

    # Open incidents
    if incidents_open > 0:
        if parts:
            parts.append(("class:status.separator", " │ "))

        parts.append(("class:status.warning", f"{Icons.WARNING} {incidents_open} incident(s)"))

    # CWD (right-aligned, would need toolbar for proper alignment)
    # Skipping for now as it needs special handling

    return FormattedText(parts)


def create_prompt_session(
    *,
    history_file: "Path | None" = None,
    enable_history_search: bool = True,
    enable_auto_suggest: bool = True,
    enable_completion: bool = True,
    multiline: bool = False,
) -> PromptSession:
    """Create a configured prompt session.

    Args:
        history_file: Path to history file (None for no persistence)
        enable_history_search: Enable Ctrl+R history search
        enable_auto_suggest: Enable ghost text suggestions
        enable_completion: Enable tab completion
        multiline: Enable multiline input mode

    Returns:
        Configured PromptSession
    """
    # History
    history = FileHistory(str(history_file)) if history_file else None

    # Key bindings
    bindings = KeyBindings()

    @bindings.add("c-l")
    def clear_screen(event):
        """Clear screen on Ctrl+L."""
        event.app.renderer.clear()

    # Build session
    session: PromptSession = PromptSession(
        style=PROMPT_STYLE,
        history=history,
        auto_suggest=AutoSuggestFromHistory() if enable_auto_suggest else None,
        completer=ElleCompleter() if enable_completion else None,
        complete_while_typing=False,  # Only complete on Tab
        key_bindings=bindings,
        enable_history_search=enable_history_search,
        multiline=multiline,
        mouse_support=False,  # Keep it simple
    )

    return session


class EllePrompt:
    """ELLE REPL prompt manager.

    Provides a stateful prompt with status bar updates.

    Example:
        prompt = EllePrompt(history_file=Path("~/.elle_history"))

        while True:
            user_input = prompt.prompt()
            result = process(user_input)
            prompt.set_state("success" if result.success else "error")
    """

    def __init__(
        self,
        *,
        history_file: "Path | None" = None,
        show_status_bar: bool = True,
    ) -> None:
        """Initialize the prompt.

        Args:
            history_file: Path to history file
            show_status_bar: Whether to show status bar above prompt
        """
        self.session = create_prompt_session(history_file=history_file)
        self.show_status_bar = show_status_bar

        # State
        self._state: Literal["idle", "success", "warning", "error"] = "idle"
        self._model: str | None = None
        self._daemon_status: str | None = None
        self._vault_docs: int | None = None
        self._incidents_open: int = 0

    def set_state(self, state: Literal["idle", "success", "warning", "error"]) -> None:
        """Set the prompt state indicator."""
        self._state = state

    def update_status(
        self,
        *,
        model: str | None = None,
        daemon_status: str | None = None,
        vault_docs: int | None = None,
        incidents_open: int | None = None,
    ) -> None:
        """Update status bar values.

        Args:
            model: LLM model name (None to keep current)
            daemon_status: Daemon status (None to keep current)
            vault_docs: Vault document count (None to keep current)
            incidents_open: Open incident count (None to keep current)
        """
        if model is not None:
            self._model = model
        if daemon_status is not None:
            self._daemon_status = daemon_status
        if vault_docs is not None:
            self._vault_docs = vault_docs
        if incidents_open is not None:
            self._incidents_open = incidents_open

    def _get_bottom_toolbar(self) -> FormattedText | None:
        """Generate bottom toolbar content."""
        if not self.show_status_bar:
            return None

        return get_status_bar(
            model=self._model,
            daemon_status=self._daemon_status,
            vault_docs=self._vault_docs,
            incidents_open=self._incidents_open,
        )

    def prompt(self, message: str | None = None) -> str:
        """Display prompt and get user input.

        Args:
            message: Optional override message (uses default if None)

        Returns:
            User input string

        Raises:
            EOFError: On Ctrl+D
            KeyboardInterrupt: On Ctrl+C
        """
        prompt_text = get_prompt_text(state=self._state)

        return self.session.prompt(
            prompt_text,
            bottom_toolbar=self._get_bottom_toolbar,
        )

    def prompt_confirm(
        self,
        message: str,
        *,
        default: bool = False,
    ) -> bool:
        """Display a yes/no confirmation prompt.

        Args:
            message: Confirmation message
            default: Default answer

        Returns:
            True if confirmed, False otherwise
        """
        suffix = " [Y/n] " if default else " [y/N] "

        while True:
            response = self.session.prompt(
                FormattedText([("class:prompt", message + suffix)]),
                bottom_toolbar=self._get_bottom_toolbar,
            ).strip().lower()

            if not response:
                return default
            if response in ("y", "yes"):
                return True
            if response in ("n", "no"):
                return False

    def prompt_choice(
        self,
        message: str,
        choices: list[tuple[str, str]],
        *,
        default: str | None = None,
    ) -> str:
        """Display a choice prompt.

        Args:
            message: Prompt message
            choices: List of (key, description) tuples
            default: Default choice key

        Returns:
            Selected choice key
        """
        # Build choice display
        choice_display = " / ".join(f"[{key}] {desc}" for key, desc in choices)
        valid_keys = {key.lower() for key, _ in choices}

        while True:
            response = self.session.prompt(
                FormattedText([
                    ("class:prompt", message + "\n"),
                    ("class:prompt.chevron", choice_display + "\n"),
                    ("class:prompt", "> "),
                ]),
                bottom_toolbar=self._get_bottom_toolbar,
            ).strip().lower()

            if not response and default:
                return default
            if response in valid_keys:
                return response

    def prompt_env_var(
        self,
        name: str,
        *,
        required: bool = False,
        sensitive: bool = False,
        default: str | None = None,
        saved_value: str | None = None,
        hint: str | None = None,
    ) -> tuple[str, bool]:
        """Prompt for a Docker environment variable value.

        Args:
            name: Variable name
            required: Whether the variable is required
            sensitive: Whether to mask input (for passwords)
            default: Default value to use if empty input
            saved_value: Previously saved value
            hint: Additional hint text to display

        Returns:
            Tuple of (value, from_saved) where from_saved indicates
            if the saved value was used.

        Raises:
            EOFError: On Ctrl+D
            KeyboardInterrupt: On Ctrl+C
        """
        import getpass

        # Build prompt label
        parts = [name]
        if required:
            parts.append("(required)")
        if sensitive:
            parts.append("(sensitive)")
        prompt_label = " ".join(parts) + ": "

        # Show hint if provided
        if hint:
            # Use print since Rich console may not be available here
            print(f"  {hint}")
        elif saved_value:
            masked = "********" if sensitive else saved_value
            print(f"  [saved: {masked}, press Enter to use]")
        elif default:
            print(f"  [default: {default}]")

        while True:
            # Get input (masked for sensitive values)
            if sensitive:
                value = getpass.getpass(f"  {prompt_label}")
            else:
                value = self.session.prompt(
                    FormattedText([("class:prompt", f"  {prompt_label}")]),
                    bottom_toolbar=self._get_bottom_toolbar,
                ).strip()

            # Handle empty input
            if not value:
                if saved_value is not None:
                    return saved_value, True
                elif default is not None:
                    return default, False
                elif required:
                    print("  This variable is required.")
                    continue
                else:
                    return "", False

            return value, False

    def prompt_env_var_batch(
        self,
        specs: list[dict],
        *,
        saved_values: dict[str, str] | None = None,
    ) -> dict[str, tuple[str, bool]]:
        """Prompt for multiple environment variables.

        Args:
            specs: List of dicts with keys: name, required, sensitive, default, description
            saved_values: Dict of previously saved values

        Returns:
            Dict mapping variable names to (value, from_saved) tuples.
            Variables that were skipped are not included.
        """
        saved = saved_values or {}
        results: dict[str, tuple[str, bool]] = {}

        # Separate required and optional
        required_specs = [s for s in specs if s.get("required", False)]
        optional_specs = [s for s in specs if not s.get("required", False)]

        # Prompt required first
        for spec in required_specs:
            name = spec["name"]
            try:
                value, from_saved = self.prompt_env_var(
                    name,
                    required=True,
                    sensitive=spec.get("sensitive", False),
                    default=spec.get("default"),
                    saved_value=saved.get(name),
                    hint=spec.get("description"),
                )
                if value:
                    results[name] = (value, from_saved)
            except (EOFError, KeyboardInterrupt):
                break

        # Then optional
        if optional_specs:
            print("\nOptional variables (press Enter to skip):")
            for spec in optional_specs:
                name = spec["name"]
                try:
                    value, from_saved = self.prompt_env_var(
                        name,
                        required=False,
                        sensitive=spec.get("sensitive", False),
                        default=spec.get("default"),
                        saved_value=saved.get(name),
                        hint=spec.get("description"),
                    )
                    if value:
                        results[name] = (value, from_saved)
                except (EOFError, KeyboardInterrupt):
                    break

        return results
