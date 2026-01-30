"""Map ELLE's cli/ui/theme.py design tokens to Textual CSS variables.

This module bridges the existing Rich-based theme system to Textual's
CSS design system, ensuring visual consistency between the old REPL
and the new TUI.
"""

from __future__ import annotations

from elle.cli.ui.theme import Colors

# Textual CSS design variables mapped from ELLE theme tokens.
# These are injected into the app via ElleApp.design.
DESIGN = {
    "dark": True,
    "primary": Colors.ACCENT,
    "secondary": Colors.ACCENT_DIM,
    "accent": Colors.ACCENT,
    "success": Colors.SUCCESS,
    "warning": Colors.WARNING,
    "error": Colors.ERROR,
}

# CSS color variables for use in TCSS files.
# Reference these via e.g. $accent in app.tcss.
CSS_VARIABLES = {
    "accent": Colors.ACCENT,
    "accent-dim": Colors.ACCENT_DIM,
    "success": Colors.SUCCESS,
    "warning": Colors.WARNING,
    "error": Colors.ERROR,
    "info": Colors.INFO,
    "muted": Colors.MUTED,
    "border": Colors.BORDER,
    "border-focus": Colors.BORDER_FOCUS,
    "highlight": Colors.HIGHLIGHT,
}
