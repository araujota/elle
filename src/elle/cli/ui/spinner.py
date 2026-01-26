"""Loading Spinners and Progress Indicators.

Provides contextual spinners that show what ELLE is doing.
Design rule: Never show a spinner for <100ms tasks.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.status import Status

from elle.cli.ui.console import console
from elle.cli.ui.theme import Colors, Theme

if TYPE_CHECKING:
    from collections.abc import Sequence


# Minimum time before showing spinner (prevents flicker)
SPINNER_DELAY_MS: int = 150


class PhaseSpinner:
    """Multi-phase spinner that shows progress through stages.

    Example:
        phases = ["Classifying intent", "Searching vault", "Generating response"]
        with PhaseSpinner(phases) as spinner:
            # ... do work ...
            spinner.advance()  # Move to next phase
            # ... more work ...
            spinner.advance()
    """

    def __init__(
        self,
        phases: Sequence[str],
        *,
        transient: bool = True,
    ) -> None:
        """Initialize phase spinner.

        Args:
            phases: List of phase descriptions
            transient: If True, spinner disappears when done
        """
        self.phases = list(phases)
        self.current_phase = 0
        self.transient = transient
        self._status: Status | None = None
        self._start_time: float = 0

    def _current_message(self) -> str:
        """Build current status message with phase indicator."""
        if not self.phases:
            return "Working..."

        phase = self.phases[self.current_phase]
        total = len(self.phases)
        current = self.current_phase + 1

        # Show phase progress: [1/3] Classifying intent...
        return f"[muted]\\[{current}/{total}][/muted] {phase}..."

    def start(self) -> None:
        """Start the spinner."""
        self._start_time = time.monotonic()
        self._status = console.status(
            self._current_message(),
            spinner=Theme.SPINNER_STYLE,
            spinner_style=Colors.ACCENT,
        )
        self._status.start()

    def advance(self, message: str | None = None) -> None:
        """Advance to next phase.

        Args:
            message: Optional override message for next phase
        """
        if self.current_phase < len(self.phases) - 1:
            self.current_phase += 1

        if message:
            self.phases[self.current_phase] = message

        if self._status:
            self._status.update(self._current_message())

    def update(self, message: str) -> None:
        """Update current phase message without advancing."""
        if self.phases:
            self.phases[self.current_phase] = message
        if self._status:
            self._status.update(self._current_message())

    def stop(self) -> None:
        """Stop the spinner."""
        if self._status:
            self._status.stop()
            self._status = None

    def __enter__(self) -> PhaseSpinner:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()


@contextmanager
def spinner(
    message: str,
    *,
    done_message: str | None = None,
    show_elapsed: bool = False,
) -> Iterator[Status]:
    """Simple spinner context manager.

    Args:
        message: Status message to display
        done_message: Optional message to show when complete
        show_elapsed: Show elapsed time when done

    Yields:
        Rich Status object for manual updates if needed

    Example:
        with spinner("Searching Man Vault"):
            results = search_vault(query)
    """
    start = time.monotonic()

    status = console.status(
        f"{message}...",
        spinner=Theme.SPINNER_STYLE,
        spinner_style=Colors.ACCENT,
    )

    try:
        status.start()
        yield status
    finally:
        status.stop()
        if done_message:
            elapsed = ""
            if show_elapsed:
                elapsed_sec = time.monotonic() - start
                elapsed = f" [muted]({elapsed_sec:.1f}s)[/muted]"
            console.print(f"[success]{done_message}[/success]{elapsed}")


@asynccontextmanager
async def async_spinner(
    message: str,
    *,
    done_message: str | None = None,
    show_elapsed: bool = False,
) -> AsyncIterator[Status]:
    """Async version of spinner context manager.

    Example:
        async with async_spinner("Generating fix"):
            fix = await generate_fix(error)
    """
    start = time.monotonic()

    status = console.status(
        f"{message}...",
        spinner=Theme.SPINNER_STYLE,
        spinner_style=Colors.ACCENT,
    )

    try:
        status.start()
        yield status
    finally:
        status.stop()
        if done_message:
            elapsed = ""
            if show_elapsed:
                elapsed_sec = time.monotonic() - start
                elapsed = f" [muted]({elapsed_sec:.1f}s)[/muted]"
            console.print(f"[success]{done_message}[/success]{elapsed}")


def progress_bar(
    total: int,
    *,
    description: str = "Processing",
    transient: bool = True,
) -> Progress:
    """Create a progress bar for known-length operations.

    Args:
        total: Total number of items
        description: Task description
        transient: If True, bar disappears when done

    Returns:
        Rich Progress context manager

    Example:
        with progress_bar(len(items), description="Indexing") as progress:
            task = progress.add_task("Indexing", total=len(items))
            for item in items:
                process(item)
                progress.update(task, advance=1)
    """
    return Progress(
        SpinnerColumn(spinner_name=Theme.SPINNER_STYLE, style=Colors.ACCENT),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30, style=Colors.ACCENT_DIM, complete_style=Colors.ACCENT),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=transient,
    )


# Predefined spinner messages for common operations
class SpinnerMessages:
    """Standard spinner messages for consistency."""

    # Intent classification
    CLASSIFYING = "Classifying intent"

    # Man Vault
    SEARCHING_VAULT = "Searching Man Vault"
    INDEXING_DOCS = "Indexing documentation"

    # Incident Vault
    SEARCHING_INCIDENTS = "Searching similar incidents"
    LINKING_INCIDENTS = "Linking to similar incidents"

    # Fixit
    DIAGNOSING = "Diagnosing failure"
    GENERATING_FIX = "Generating fix"
    VERIFYING_FIX = "Verifying fix"

    # Planner
    GENERATING_PLAN = "Generating plan"
    VERIFYING_STEP = "Verifying step"

    # Config generation
    GENERATING_CONFIG = "Generating configuration"
    VALIDATING_CONFIG = "Validating configuration"

    # General
    CONNECTING = "Connecting to Ollama"
    PROCESSING = "Processing"
    LOADING = "Loading"


# Convenience functions for common operations


@contextmanager
def classifying() -> Iterator[Status]:
    """Spinner for intent classification."""
    with spinner(SpinnerMessages.CLASSIFYING) as s:
        yield s


@contextmanager
def searching_vault() -> Iterator[Status]:
    """Spinner for Man Vault search."""
    with spinner(SpinnerMessages.SEARCHING_VAULT) as s:
        yield s


@contextmanager
def diagnosing() -> Iterator[Status]:
    """Spinner for fixit diagnosis."""
    with spinner(SpinnerMessages.DIAGNOSING) as s:
        yield s


@contextmanager
def generating_plan() -> Iterator[Status]:
    """Spinner for plan generation."""
    with spinner(SpinnerMessages.GENERATING_PLAN) as s:
        yield s


def fixit_phases() -> PhaseSpinner:
    """Multi-phase spinner for full fixit flow."""
    return PhaseSpinner(
        [
            "Analyzing failure",
            "Searching similar incidents",
            "Querying documentation",
            "Generating fix",
            "Verifying safety",
        ]
    )


def planner_phases() -> PhaseSpinner:
    """Multi-phase spinner for full planning flow."""
    return PhaseSpinner(
        [
            "Analyzing request",
            "Searching documentation",
            "Checking prior art",
            "Generating plan",
            "Validating steps",
        ]
    )


def classification_phases() -> PhaseSpinner:
    """Multi-phase spinner for intent classification."""
    return PhaseSpinner(
        [
            "Parsing input",
            "Classifying intent",
            "Checking safety",
        ]
    )
