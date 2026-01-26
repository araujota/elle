"""Docker Environment Variable Prompting Session.

Interactive prompting for Docker environment variables with:
- Box-drawing UI showing all variables
- One-at-a-time input with sensitive value masking
- Option to explain variables
- Summary confirmation before proceeding
"""

from __future__ import annotations

import getpass
from typing import TYPE_CHECKING, Literal

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from elle.cli.docker.env_models import (
    EnvVarPromptResult,
    EnvVarSpec,
    EnvVarValue,
)
from elle.cli.docker.env_profiles import extract_image_family
from elle.cli.docker.env_store import DockerEnvStore
from elle.cli.ui.theme import Icons

if TYPE_CHECKING:
    from elle.cli.docker.env_explainer import EnvVarExplainer


# Prompt session state
PromptState = Literal["overview", "input", "summary", "done", "cancelled"]


class EnvVarPromptSession:
    """Interactive session for prompting Docker environment variables.

    Guides the user through entering values for required and optional
    environment variables with a friendly UI.

    Example:
        session = EnvVarPromptSession(
            specs=(
                EnvVarSpec(name="POSTGRES_PASSWORD", required=True, sensitive=True),
                EnvVarSpec(name="POSTGRES_USER", required=False, default="postgres"),
            ),
            image="postgres:15",
        )
        result = session.run()

        if not result.cancelled:
            for name, value in result.to_env_tuple():
                print(f"-e {name}={value}")
    """

    def __init__(
        self,
        specs: tuple[EnvVarSpec, ...],
        image: str,
        *,
        console: Console | None = None,
        store: DockerEnvStore | None = None,
        explainer: EnvVarExplainer | None = None,
        use_saved: bool = True,
        save_values: bool = True,
    ) -> None:
        """Initialize the prompting session.

        Args:
            specs: Environment variable specifications to prompt for.
            image: Docker image name.
            console: Rich console for output (creates new if None).
            store: Store for saved values (creates new if None).
            explainer: Explainer for variable descriptions (optional).
            use_saved: Whether to offer previously saved values.
            save_values: Whether to save entered values for future use.
        """
        self.specs = specs
        self.image = image
        self.image_family = extract_image_family(image)
        self.console = console or Console()
        self.store = store
        self.explainer = explainer
        self.use_saved = use_saved
        self.save_values = save_values

        # Session state
        self._state: PromptState = "overview"
        self._collected: dict[str, EnvVarValue] = {}
        self._skipped: set[str] = set()
        self._from_saved: set[str] = set()

    def run(self) -> EnvVarPromptResult:
        """Run the interactive prompting session.

        Returns:
            EnvVarPromptResult with collected values.
        """
        # Load saved values if available
        saved_values: dict[str, str] = {}
        if self.use_saved and self.store:
            for saved in self.store.get_saved_values(self.image):
                saved_values[saved.name] = saved.value

        # Show overview panel
        self._show_overview_panel(saved_values)

        # Get initial action
        action = self._prompt_initial_action(bool(saved_values))

        if action == "q":
            return EnvVarPromptResult(cancelled=True, image=self.image)

        if action == "s":
            # Skip all
            return EnvVarPromptResult(
                skipped=tuple(spec.name for spec in self.specs),
                image=self.image,
            )

        if action == "u" and saved_values:
            # Use saved values
            for spec in self.specs:
                if spec.name in saved_values:
                    self._collected[spec.name] = EnvVarValue(
                        name=spec.name,
                        value=saved_values[spec.name],
                        from_saved=True,
                        sensitive=spec.sensitive,
                    )
                    self._from_saved.add(spec.name)

            # Prompt for any missing required
            missing_required = [spec for spec in self.specs if spec.required and spec.name not in self._collected]
            if missing_required:
                self.console.print()
                self.console.print(Text("Some required variables are missing:", style="warning"))
                for spec in missing_required:
                    self._prompt_single_var(spec, saved_values)
        else:
            # Enter values interactively
            self._prompt_all_vars(saved_values)

        # Show summary and confirm
        if self._collected:
            confirmed = self._show_summary_and_confirm()
            if not confirmed:
                return EnvVarPromptResult(cancelled=True, image=self.image)

            # Save values if requested
            if self.save_values and self.store:
                values_to_save = [v for v in self._collected.values() if not v.from_saved]
                if values_to_save:
                    self.store.save_values(self.image, values_to_save)

        return EnvVarPromptResult(
            values=tuple(self._collected.values()),
            skipped=tuple(self._skipped),
            image=self.image,
        )

    def _show_overview_panel(self, saved_values: dict[str, str]) -> None:
        """Display the overview panel showing all variables."""
        # Build content
        content = Text()

        # Required variables
        required = [spec for spec in self.specs if spec.required]
        optional = [spec for spec in self.specs if not spec.required]

        if required:
            content.append("Required:\n", style="bold")
            for spec in required:
                self._format_var_line(content, spec, saved_values)
            content.append("\n")

        if optional:
            content.append("Optional:\n", style="bold")
            for spec in optional:
                self._format_var_line(content, spec, saved_values)

        # Create panel
        panel = Panel(
            content,
            title="[bold cyan]Docker Environment Variables[/bold cyan]",
            subtitle=f"[dim]{self.image}[/dim]",
            border_style="cyan",
            padding=(1, 2),
        )

        self.console.print()
        self.console.print(panel)

    def _format_var_line(
        self,
        content: Text,
        spec: EnvVarSpec,
        saved_values: dict[str, str],
    ) -> None:
        """Format a single variable line for the overview."""
        content.append(f"  {Icons.BULLET} ", style="muted")
        content.append(spec.name, style="bold")

        # Annotations
        annotations: list[str] = []
        if spec.sensitive:
            annotations.append("sensitive")
        if spec.default:
            annotations.append(f"default: {spec.default}")
        if spec.name in saved_values:
            annotations.append("saved")

        if annotations:
            content.append(f" ({', '.join(annotations)})", style="dim")

        content.append("\n")

        # Description
        if spec.description:
            content.append(f"    {spec.description}\n", style="dim")

    def _prompt_initial_action(self, has_saved: bool) -> str:
        """Prompt for initial action choice.

        Returns:
            Action key: 'e' (enter), 's' (skip), 'u' (use saved), 'q' (quit)
        """
        self.console.print()

        # Build options
        options = []
        if has_saved:
            options.append(("[u]", "Use saved values"))
        options.extend(
            [
                ("[e]", "Enter values"),
                ("[s]", "Skip"),
                ("[?]", "Explain"),
                ("[q]", "Cancel"),
            ]
        )

        option_text = "  ".join(f"[cyan]{k}[/cyan] {v}" for k, v in options)
        self.console.print(option_text)
        self.console.print()

        valid_keys = {"e", "s", "?", "q"}
        if has_saved:
            valid_keys.add("u")

        while True:
            try:
                response = input("> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return "q"

            if response in valid_keys:
                if response == "?":
                    self._show_explanations()
                    self._show_overview_panel({})
                    return self._prompt_initial_action(has_saved)
                return response

            self.console.print(f"[dim]Invalid option. Valid: {', '.join(sorted(valid_keys))}[/dim]")

    def _prompt_all_vars(self, saved_values: dict[str, str]) -> None:
        """Prompt for all variables one at a time."""
        # Required first, then optional
        required = [spec for spec in self.specs if spec.required]
        optional = [spec for spec in self.specs if not spec.required]

        self.console.print()

        for spec in required:
            self._prompt_single_var(spec, saved_values)

        if optional:
            self.console.print()
            self.console.print("[dim]Optional variables (press Enter to skip):[/dim]")
            for spec in optional:
                self._prompt_single_var(spec, saved_values)

    def _prompt_single_var(
        self,
        spec: EnvVarSpec,
        saved_values: dict[str, str],
    ) -> None:
        """Prompt for a single variable value."""
        # Build prompt text
        prompt_parts = [spec.name]
        if spec.required:
            prompt_parts.append("(required)")
        if spec.sensitive:
            prompt_parts.append("(sensitive)")

        prompt_text = " ".join(prompt_parts) + ": "

        # Show default/saved hint
        hint = None
        if spec.name in saved_values:
            masked = "********" if spec.sensitive else saved_values[spec.name]
            hint = f"[dim]saved: {masked}, press Enter to use[/dim]"
        elif spec.default:
            hint = f"[dim]default: {spec.default}[/dim]"
        elif spec.example:
            hint = f"[dim]e.g., {spec.example}[/dim]"

        if hint:
            self.console.print(f"  {hint}")

        # Get input
        while True:
            try:
                if spec.sensitive:
                    # Use getpass for password masking
                    value = getpass.getpass(f"  {prompt_text}")
                else:
                    value = input(f"  {prompt_text}").strip()
            except (EOFError, KeyboardInterrupt):
                self._skipped.add(spec.name)
                return

            # Handle empty input
            if not value:
                if spec.name in saved_values:
                    # Use saved value
                    self._collected[spec.name] = EnvVarValue(
                        name=spec.name,
                        value=saved_values[spec.name],
                        from_saved=True,
                        sensitive=spec.sensitive,
                    )
                    self._from_saved.add(spec.name)
                    return
                elif spec.default:
                    # Use default
                    self._collected[spec.name] = EnvVarValue(
                        name=spec.name,
                        value=spec.default,
                        sensitive=spec.sensitive,
                    )
                    return
                elif spec.required:
                    self.console.print("  [red]This variable is required.[/red]")
                    continue
                else:
                    # Skip optional
                    self._skipped.add(spec.name)
                    return

            # Handle explain request
            if value == "?":
                self._explain_single_var(spec)
                continue

            # Validate and store
            self._collected[spec.name] = EnvVarValue(
                name=spec.name,
                value=value,
                sensitive=spec.sensitive,
            )
            return

    def _show_summary_and_confirm(self) -> bool:
        """Show summary of entered values and confirm.

        Returns:
            True if confirmed, False to cancel.
        """
        self.console.print()

        # Build summary table
        table = Table(
            show_header=True,
            header_style="bold",
            border_style="dim",
            padding=(0, 1),
        )
        table.add_column("Variable", style="cyan")
        table.add_column("Value")
        table.add_column("Source", style="dim")

        for value in self._collected.values():
            display_value = "********" if value.sensitive else value.value
            source = "saved" if value.from_saved else "entered"
            table.add_row(value.name, display_value, source)

        # Create panel
        panel = Panel(
            table,
            title="[bold]Summary[/bold]",
            border_style="green",
            padding=(1, 2),
        )

        self.console.print(panel)
        self.console.print()
        self.console.print("[cyan][c][/cyan] Confirm  [cyan][e][/cyan] Edit  [cyan][q][/cyan] Cancel")
        self.console.print()

        while True:
            try:
                response = input("> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False

            if response == "c":
                return True
            elif response == "q":
                return False
            elif response == "e":
                # Re-run prompting
                self._collected.clear()
                self._skipped.clear()
                self._from_saved.clear()
                self._prompt_all_vars({})
                return self._show_summary_and_confirm()
            else:
                self.console.print("[dim]Invalid option. Use c, e, or q.[/dim]")

    def _show_explanations(self) -> None:
        """Show explanations for all variables."""
        self.console.print()
        self.console.print("[bold]Variable Explanations:[/bold]")
        self.console.print()

        for spec in self.specs:
            self._explain_single_var(spec)

    def _explain_single_var(self, spec: EnvVarSpec) -> None:
        """Show explanation for a single variable."""
        self.console.print(f"  [bold cyan]{spec.name}[/bold cyan]")

        if spec.description:
            self.console.print(f"    {spec.description}")
        elif self.explainer:
            # Use LLM explainer for unknown vars
            explanation = self.explainer.explain(spec.name, self.image)
            if explanation:
                self.console.print(f"    {explanation}")
            else:
                self.console.print("    [dim]No description available.[/dim]")
        else:
            self.console.print("    [dim]No description available.[/dim]")

        if spec.required:
            self.console.print("    [yellow]Required[/yellow]")
        else:
            self.console.print("    [dim]Optional[/dim]")

        if spec.default:
            self.console.print(f"    Default: {spec.default}")
        if spec.example:
            self.console.print(f"    Example: {spec.example}")

        self.console.print()


# =============================================================================
# Convenience Functions
# =============================================================================


def prompt_env_vars(
    specs: tuple[EnvVarSpec, ...],
    image: str,
    *,
    console: Console | None = None,
    use_saved: bool = True,
    save_values: bool = True,
) -> EnvVarPromptResult:
    """Run an environment variable prompting session.

    Convenience function for common use case.

    Args:
        specs: Environment variable specifications.
        image: Docker image name.
        console: Rich console for output.
        use_saved: Whether to offer saved values.
        save_values: Whether to save entered values.

    Returns:
        EnvVarPromptResult with collected values.
    """
    store = DockerEnvStore() if (use_saved or save_values) else None

    session = EnvVarPromptSession(
        specs=specs,
        image=image,
        console=console,
        store=store,
        use_saved=use_saved,
        save_values=save_values,
    )

    try:
        return session.run()
    finally:
        if store:
            store.close()


def prompt_for_image(
    image: str,
    *,
    existing_env: tuple[tuple[str, str], ...] = (),
    console: Console | None = None,
) -> EnvVarPromptResult:
    """Detect and prompt for environment variables for an image.

    Combines detection and prompting into a single call.

    Args:
        image: Docker image name.
        existing_env: Environment variables already set.
        console: Rich console for output.

    Returns:
        EnvVarPromptResult with collected values.
    """
    from elle.cli.docker.env_detector import DockerEnvDetector

    detector = DockerEnvDetector()
    specs = detector.detect_from_image(image, existing_env=existing_env)

    if not specs:
        return EnvVarPromptResult(image=image)

    return prompt_env_vars(specs, image, console=console)
