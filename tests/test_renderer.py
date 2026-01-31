"""Tests for the terminal renderer."""

from __future__ import annotations

from elle.cli.terminal.renderer import Colors


class TestColors:
    """Tests for Colors constants."""

    def test_colors_has_reset(self) -> None:
        """RESET constant should be ANSI reset code."""
        assert Colors.RESET == "\033[0m"

    def test_colors_has_basic_colors(self) -> None:
        """Basic color constants should exist."""
        assert Colors.RED
        assert Colors.GREEN
        assert Colors.YELLOW
        assert Colors.BLUE
        assert Colors.MAGENTA
        assert Colors.CYAN

    def test_colors_has_styles(self) -> None:
        """Style constants should exist."""
        assert Colors.BOLD
        assert Colors.DIM

    def test_colors_has_bold_colors(self) -> None:
        """Bold color constants should exist."""
        assert Colors.BOLD_RED
        assert Colors.BOLD_GREEN
        assert Colors.BOLD_YELLOW
        assert Colors.BOLD_CYAN

    def test_colors_are_ansi_sequences(self) -> None:
        """Color constants should be valid ANSI sequences."""
        assert Colors.RESET.startswith("\033[")
        assert Colors.BOLD.startswith("\033[")
        assert Colors.RED.startswith("\033[")
