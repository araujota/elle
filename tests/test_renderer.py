"""Tests for the terminal renderer."""

from elle.cli.terminal.renderer import (
    Colors,
    render_banner,
    render_error,
    render_prompt,
    render_prompt_plain,
    render_success,
    render_warning,
)
from elle.common.session import create_session


class TestRenderPrompt:
    """Tests for prompt rendering."""

    def test_idle_prompt(self) -> None:
        """Idle prompt should show 'elle ▸'."""
        session = create_session()
        prompt = render_prompt(session)

        assert "elle" in prompt
        assert "▸" in prompt

    def test_success_prompt(self) -> None:
        """After successful command, prompt should show checkmark."""
        session = create_session().with_command_result(cmd="ls", exit_code=0)
        prompt = render_prompt(session)

        assert "✓" in prompt

    def test_warning_prompt(self) -> None:
        """With warnings, prompt should show warning indicator."""
        session = create_session()
        prompt = render_prompt(session, has_warnings=True)

        assert "⚠" in prompt

    def test_warning_takes_precedence(self) -> None:
        """Warning indicator should take precedence over success."""
        session = create_session().with_command_result(cmd="ls", exit_code=0)
        prompt = render_prompt(session, has_warnings=True)

        assert "⚠" in prompt
        assert "✓" not in prompt


class TestRenderPromptPlain:
    """Tests for plain (no color) prompt rendering."""

    def test_plain_idle(self) -> None:
        """Plain idle prompt."""
        session = create_session()
        prompt = render_prompt_plain(session)

        assert prompt == "elle ▸ "

    def test_plain_success(self) -> None:
        """Plain success prompt."""
        session = create_session().with_command_result(cmd="ls", exit_code=0)
        prompt = render_prompt_plain(session)

        assert prompt == "elle ✓ ▸ "

    def test_plain_warning(self) -> None:
        """Plain warning prompt."""
        session = create_session()
        prompt = render_prompt_plain(session, has_warnings=True)

        assert prompt == "elle ⚠ ▸ "


class TestRenderMessages:
    """Tests for message rendering functions."""

    def test_render_error(self) -> None:
        """Error messages should be formatted."""
        output = render_error("Something went wrong")

        assert "Error:" in output
        assert "Something went wrong" in output

    def test_render_success(self) -> None:
        """Success messages should include checkmark."""
        output = render_success("Operation completed")

        assert "✓" in output
        assert "Operation completed" in output

    def test_render_warning(self) -> None:
        """Warning messages should be formatted."""
        output = render_warning("Proceed with caution")

        assert "Warning:" in output
        assert "Proceed with caution" in output


class TestRenderBanner:
    """Tests for banner rendering."""

    def test_banner_content(self) -> None:
        """Banner should include ELLE branding."""
        session = create_session()
        banner = render_banner(session)

        assert "ELLE" in banner
        assert "help" in banner.lower()


class TestColors:
    """Tests for color constants."""

    def test_colors_are_ansi_sequences(self) -> None:
        """Color constants should be valid ANSI sequences."""
        assert Colors.RESET.startswith("\033[")
        assert Colors.BOLD.startswith("\033[")
        assert Colors.RED.startswith("\033[")
