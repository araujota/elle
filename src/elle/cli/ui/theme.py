"""ELLE Design System - Colors, Icons, and Theme Tokens.

Single source of truth for all visual styling in the REPL.
"""

from __future__ import annotations

from typing import Final


class Colors:
    """ELLE color palette.

    Uses a restrained palette with one accent color (cyan/teal).
    All colors are rich markup compatible.
    """

    # Primary accent - the "ELLE blue/teal"
    ACCENT: Final[str] = "cyan"
    ACCENT_DIM: Final[str] = "dark_cyan"

    # Semantic colors
    SUCCESS: Final[str] = "green"
    WARNING: Final[str] = "yellow"
    ERROR: Final[str] = "red"
    INFO: Final[str] = "blue"

    # Risk levels
    RISK_LOW: Final[str] = "green"
    RISK_MEDIUM: Final[str] = "yellow"
    RISK_HIGH: Final[str] = "red"

    # Severity colors
    SEVERITY_INFO: Final[str] = "blue"
    SEVERITY_WARNING: Final[str] = "yellow"
    SEVERITY_ERROR: Final[str] = "red"
    SEVERITY_CRITICAL: Final[str] = "bold red"

    # Outcome colors
    OUTCOME_IMPROVED: Final[str] = "green"
    OUTCOME_PARTIAL: Final[str] = "yellow"
    OUTCOME_NO_CHANGE: Final[str] = "dim"
    OUTCOME_WORSE: Final[str] = "red"

    # UI elements
    BORDER: Final[str] = "dim"
    BORDER_FOCUS: Final[str] = "cyan"
    MUTED: Final[str] = "dim"
    HIGHLIGHT: Final[str] = "bold cyan"
    CODE: Final[str] = "bright_white on grey23"
    PATH: Final[str] = "cyan"
    COMMAND: Final[str] = "bold bright_white"

    # Domain colors (for incident domains)
    DOMAIN_NET: Final[str] = "blue"
    DOMAIN_DISK: Final[str] = "yellow"
    DOMAIN_OOM: Final[str] = "red"
    DOMAIN_DOCKER: Final[str] = "cyan"
    DOMAIN_AUTH: Final[str] = "magenta"
    DOMAIN_PKG: Final[str] = "green"
    DOMAIN_SERVICE: Final[str] = "blue"
    DOMAIN_FS: Final[str] = "yellow"


class Icons:
    """Unicode icons/glyphs for consistent visual language.

    Keep it minimal - too many icons creates visual noise.
    """

    # Status indicators
    SUCCESS: Final[str] = "✓"
    WARNING: Final[str] = "⚠"
    ERROR: Final[str] = "✖"
    INFO: Final[str] = "ℹ"
    PENDING: Final[str] = "○"
    IN_PROGRESS: Final[str] = "◐"
    COMPLETE: Final[str] = "●"

    # Navigation/action
    ARROW_RIGHT: Final[str] = "→"
    ARROW_LEFT: Final[str] = "←"
    ARROW_UP: Final[str] = "↑"
    ARROW_DOWN: Final[str] = "↓"
    CHEVRON: Final[str] = "▸"
    BULLET: Final[str] = "•"

    # Prompt indicators
    PROMPT_IDLE: Final[str] = "▸"
    PROMPT_SUCCESS: Final[str] = "✓"
    PROMPT_WARNING: Final[str] = "⚠"
    PROMPT_ERROR: Final[str] = "✖"

    # Domain icons (for incident categories)
    DOMAIN_NET: Final[str] = "⚡"
    DOMAIN_DISK: Final[str] = "⛁"
    DOMAIN_OOM: Final[str] = "⚠"
    DOMAIN_DOCKER: Final[str] = "⚓"
    DOMAIN_AUTH: Final[str] = "🔐"
    DOMAIN_PKG: Final[str] = "☕"
    DOMAIN_SERVICE: Final[str] = "⚙"
    DOMAIN_FS: Final[str] = "📁"

    # Risk levels
    RISK_LOW: Final[str] = "○"
    RISK_MEDIUM: Final[str] = "◐"
    RISK_HIGH: Final[str] = "●"

    # Misc
    SPINNER: Final[str] = "⌁"
    LINK: Final[str] = "🔗"
    CLOCK: Final[str] = "⏱"
    LOCK: Final[str] = "🔒"
    KEY: Final[str] = "🔑"
    WRENCH: Final[str] = "🔧"
    MAGNIFIER: Final[str] = "🔍"
    BOOK: Final[str] = "📖"
    LIGHTBULB: Final[str] = "💡"
    FIRE: Final[str] = "🔥"
    SHIELD: Final[str] = "🛡"
    CITATION: Final[str] = "📌"
    HISTORY: Final[str] = "📜"
    BRAIN: Final[str] = "🧠"

    @classmethod
    def domain_icon(cls, domain: str) -> str:
        """Get icon for incident domain."""
        return {
            "net": cls.DOMAIN_NET,
            "disk": cls.DOMAIN_DISK,
            "oom": cls.DOMAIN_OOM,
            "docker": cls.DOMAIN_DOCKER,
            "auth": cls.DOMAIN_AUTH,
            "pkg": cls.DOMAIN_PKG,
            "service": cls.DOMAIN_SERVICE,
            "fs": cls.DOMAIN_FS,
        }.get(domain, cls.BULLET)

    @classmethod
    def risk_icon(cls, risk: str) -> str:
        """Get icon for risk level."""
        return {
            "low": cls.RISK_LOW,
            "medium": cls.RISK_MEDIUM,
            "high": cls.RISK_HIGH,
        }.get(risk.lower(), cls.BULLET)


class Spacing:
    """Consistent spacing values."""

    # Panel padding
    PANEL_PADDING: Final[tuple[int, int]] = (1, 2)  # (vertical, horizontal)

    # Table cell padding
    TABLE_PADDING: Final[tuple[int, int]] = (0, 1)

    # Section margins
    SECTION_MARGIN: Final[int] = 1

    # Max widths
    MAX_PANEL_WIDTH: Final[int] = 80
    MAX_TABLE_WIDTH: Final[int] = 120
    NARROW_WIDTH: Final[int] = 60


class Theme:
    """Consolidated theme access."""

    colors = Colors
    icons = Icons
    spacing = Spacing

    # Panel box styles (rich box types)
    PANEL_BOX: Final[str] = "ROUNDED"
    PANEL_BOX_FOCUS: Final[str] = "DOUBLE"
    TABLE_BOX: Final[str] = "SIMPLE"

    # Spinner styles
    SPINNER_STYLE: Final[str] = "dots"

    @classmethod
    def severity_color(cls, severity: str) -> str:
        """Get color for severity level."""
        return {
            "info": Colors.SEVERITY_INFO,
            "warning": Colors.SEVERITY_WARNING,
            "error": Colors.SEVERITY_ERROR,
            "critical": Colors.SEVERITY_CRITICAL,
        }.get(severity.lower(), Colors.MUTED)

    @classmethod
    def outcome_color(cls, outcome: str) -> str:
        """Get color for incident outcome."""
        return {
            "improved": Colors.OUTCOME_IMPROVED,
            "partial": Colors.OUTCOME_PARTIAL,
            "no_change": Colors.OUTCOME_NO_CHANGE,
            "worse": Colors.OUTCOME_WORSE,
        }.get(outcome.lower(), Colors.MUTED)

    @classmethod
    def risk_color(cls, risk: str) -> str:
        """Get color for risk level."""
        return {
            "low": Colors.RISK_LOW,
            "medium": Colors.RISK_MEDIUM,
            "high": Colors.RISK_HIGH,
        }.get(risk.lower(), Colors.MUTED)

    @classmethod
    def domain_color(cls, domain: str) -> str:
        """Get color for incident domain."""
        return {
            "net": Colors.DOMAIN_NET,
            "disk": Colors.DOMAIN_DISK,
            "oom": Colors.DOMAIN_OOM,
            "docker": Colors.DOMAIN_DOCKER,
            "auth": Colors.DOMAIN_AUTH,
            "pkg": Colors.DOMAIN_PKG,
            "service": Colors.DOMAIN_SERVICE,
            "fs": Colors.DOMAIN_FS,
        }.get(domain.lower(), Colors.MUTED)
