"""Legacy ANSI color codes for ELLE Terminal.

DEPRECATED: New code should use ``elle.cli.ui.theme`` with Rich markup.
This module is retained only for the ``Colors`` class, which is still
referenced by code that writes raw ANSI escape sequences.
"""

from __future__ import annotations


class Colors:
    """ANSI escape codes for terminal colors.

    DEPRECATED: Use ``elle.cli.ui.theme.Colors`` with Rich markup instead.
    """

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"

    BOLD_RED = "\033[1;31m"
    BOLD_GREEN = "\033[1;32m"
    BOLD_YELLOW = "\033[1;33m"
    BOLD_CYAN = "\033[1;36m"
