from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.docker.env_models import (
    EnvVarPromptResult,
    EnvVarSpec,
    EnvVarValue,
    SavedEnvVar,
)
from elle.cli.docker.env_prompt import (
    EnvVarPromptSession,
    prompt_env_vars,
    prompt_for_image,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_specs() -> tuple[EnvVarSpec, ...]:
    return (
        EnvVarSpec(name="DB_PASSWORD", required=True, sensitive=True, description="Database password"),
        EnvVarSpec(name="DB_USER", required=False, default="postgres", description="Database user"),
        EnvVarSpec(name="DB_HOST", required=False, example="localhost"),
    )


def _make_session(
    specs=None,
    image="postgres:15",
    store=None,
    explainer=None,
    use_saved=True,
    save_values=True,
) -> EnvVarPromptSession:
    from rich.console import Console

    console = Console(file=StringIO())
    return EnvVarPromptSession(
        specs=specs or _make_specs(),
        image=image,
        console=console,
        store=store,
        explainer=explainer,
        use_saved=use_saved,
        save_values=save_values,
    )


# ---------------------------------------------------------------------------
# EnvVarPromptSession init
# ---------------------------------------------------------------------------


class TestEnvVarPromptSessionInit:
    def test_init(self):
        session = _make_session()
        assert session.image == "postgres:15"
        assert len(session.specs) == 3
        assert session._state == "overview"


# ---------------------------------------------------------------------------
# run - quit
# ---------------------------------------------------------------------------


class TestSessionRunQuit:
    def test_quit_returns_cancelled(self):
        session = _make_session()
        with (
            patch.object(session, "_show_overview_panel"),
            patch.object(session, "_prompt_initial_action", return_value="q"),
        ):
            result = session.run()
        assert result.cancelled is True

    def test_skip_returns_skipped(self):
        session = _make_session()
        with (
            patch.object(session, "_show_overview_panel"),
            patch.object(session, "_prompt_initial_action", return_value="s"),
        ):
            result = session.run()
        assert len(result.skipped) == 3
        assert result.cancelled is False

    def test_use_saved_values(self):
        mock_store = MagicMock()
        from datetime import datetime, timezone

        mock_store.get_saved_values.return_value = [
            SavedEnvVar(
                image_family="postgres",
                name="DB_PASSWORD",
                value="secret",
                sensitive=True,
                updated_at=datetime.now(timezone.utc),
            ),
        ]
        session = _make_session(store=mock_store)
        with (
            patch.object(session, "_show_overview_panel"),
            patch.object(session, "_prompt_initial_action", return_value="u"),
            patch.object(session, "_prompt_single_var"),
            patch.object(session, "_show_summary_and_confirm", return_value=True),
        ):
            result = session.run()
        # DB_PASSWORD should be collected from saved
        assert "DB_PASSWORD" in {v.name for v in result.values}

    def test_enter_values(self):
        session = _make_session()
        with (
            patch.object(session, "_show_overview_panel"),
            patch.object(session, "_prompt_initial_action", return_value="e"),
            patch.object(session, "_prompt_all_vars") as mock_prompt_all,
        ):
            # Simulate user entering a value
            def add_value(saved):
                session._collected["DB_PASSWORD"] = EnvVarValue(
                    name="DB_PASSWORD",
                    value="mypass",
                    sensitive=True,
                )

            mock_prompt_all.side_effect = add_value
            with patch.object(session, "_show_summary_and_confirm", return_value=True):
                result = session.run()
        assert len(result.values) == 1

    def test_enter_then_cancel(self):
        session = _make_session()
        with (
            patch.object(session, "_show_overview_panel"),
            patch.object(session, "_prompt_initial_action", return_value="e"),
            patch.object(session, "_prompt_all_vars") as mock_prompt_all,
        ):

            def add_value(saved):
                session._collected["DB_PASSWORD"] = EnvVarValue(
                    name="DB_PASSWORD",
                    value="mypass",
                    sensitive=True,
                )

            mock_prompt_all.side_effect = add_value
            with patch.object(session, "_show_summary_and_confirm", return_value=False):
                result = session.run()
        assert result.cancelled is True

    def test_save_values_on_confirm(self):
        mock_store = MagicMock()
        mock_store.get_saved_values.return_value = []
        session = _make_session(store=mock_store, save_values=True)
        with (
            patch.object(session, "_show_overview_panel"),
            patch.object(session, "_prompt_initial_action", return_value="e"),
            patch.object(session, "_prompt_all_vars") as mock_prompt_all,
        ):

            def add_value(saved):
                session._collected["DB_PASSWORD"] = EnvVarValue(
                    name="DB_PASSWORD",
                    value="mypass",
                    sensitive=True,
                )

            mock_prompt_all.side_effect = add_value
            with patch.object(session, "_show_summary_and_confirm", return_value=True):
                session.run()
        mock_store.save_values.assert_called_once()

    def test_no_collected_returns_empty(self):
        session = _make_session()
        with (
            patch.object(session, "_show_overview_panel"),
            patch.object(session, "_prompt_initial_action", return_value="e"),
            patch.object(session, "_prompt_all_vars"),
        ):
            result = session.run()
        assert len(result.values) == 0
        assert not result.cancelled

    def test_use_saved_with_missing_required(self):
        mock_store = MagicMock()
        from datetime import datetime, timezone

        mock_store.get_saved_values.return_value = [
            SavedEnvVar(
                image_family="postgres",
                name="DB_USER",
                value="admin",
                updated_at=datetime.now(timezone.utc),
            ),
        ]
        session = _make_session(store=mock_store)
        with (
            patch.object(session, "_show_overview_panel"),
            patch.object(session, "_prompt_initial_action", return_value="u"),
            patch.object(session, "_prompt_single_var") as mock_prompt_single,
            patch.object(session, "_show_summary_and_confirm", return_value=True),
        ):

            def add_required(spec, saved):
                session._collected["DB_PASSWORD"] = EnvVarValue(
                    name="DB_PASSWORD",
                    value="pass",
                    sensitive=True,
                )

            mock_prompt_single.side_effect = add_required
            result = session.run()
        # Both DB_USER (saved) and DB_PASSWORD (prompted) should be collected
        names = {v.name for v in result.values}
        assert "DB_USER" in names
        assert "DB_PASSWORD" in names


# ---------------------------------------------------------------------------
# _format_var_line
# ---------------------------------------------------------------------------


class TestFormatVarLine:
    def test_format_sensitive(self):
        from rich.text import Text

        session = _make_session()
        content = Text()
        spec = EnvVarSpec(name="SECRET", sensitive=True, required=True)
        session._format_var_line(content, spec, {})
        plain = content.plain
        assert "SECRET" in plain
        assert "sensitive" in plain

    def test_format_with_saved(self):
        from rich.text import Text

        session = _make_session()
        content = Text()
        spec = EnvVarSpec(name="KEY")
        session._format_var_line(content, spec, {"KEY": "val"})
        plain = content.plain
        assert "saved" in plain

    def test_format_with_default(self):
        from rich.text import Text

        session = _make_session()
        content = Text()
        spec = EnvVarSpec(name="KEY", default="defval")
        session._format_var_line(content, spec, {})
        plain = content.plain
        assert "default: defval" in plain

    def test_format_with_description(self):
        from rich.text import Text

        session = _make_session()
        content = Text()
        spec = EnvVarSpec(name="KEY", description="A description")
        session._format_var_line(content, spec, {})
        plain = content.plain
        assert "A description" in plain


# ---------------------------------------------------------------------------
# _prompt_initial_action
# ---------------------------------------------------------------------------


class TestPromptInitialAction:
    def test_valid_input(self):
        session = _make_session()
        with patch("builtins.input", return_value="e"):
            result = session._prompt_initial_action(False)
        assert result == "e"

    def test_eof_returns_quit(self):
        session = _make_session()
        with patch("builtins.input", side_effect=EOFError):
            result = session._prompt_initial_action(False)
        assert result == "q"

    def test_keyboard_interrupt(self):
        session = _make_session()
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            result = session._prompt_initial_action(False)
        assert result == "q"

    def test_explain_recurses(self):
        session = _make_session()
        call_count = 0

        def side_effect(prompt=""):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "?"
            return "e"

        with (
            patch("builtins.input", side_effect=side_effect),
            patch.object(session, "_show_explanations"),
            patch.object(session, "_show_overview_panel"),
        ):
            result = session._prompt_initial_action(False)
        assert result == "e"

    def test_invalid_then_valid(self):
        session = _make_session()
        inputs = iter(["x", "z", "e"])
        with patch("builtins.input", side_effect=lambda *a: next(inputs)):
            result = session._prompt_initial_action(False)
        assert result == "e"

    def test_use_saved_option(self):
        session = _make_session()
        with patch("builtins.input", return_value="u"):
            result = session._prompt_initial_action(True)
        assert result == "u"


# ---------------------------------------------------------------------------
# _prompt_single_var
# ---------------------------------------------------------------------------


class TestPromptSingleVar:
    def test_enter_value(self):
        session = _make_session()
        spec = EnvVarSpec(name="MY_VAR", required=True)
        with patch("builtins.input", return_value="hello"):
            session._prompt_single_var(spec, {})
        assert session._collected["MY_VAR"].value == "hello"

    def test_sensitive_uses_getpass(self):
        session = _make_session()
        spec = EnvVarSpec(name="SECRET", required=True, sensitive=True)
        with patch("elle.cli.docker.env_prompt.getpass.getpass", return_value="s3cret"):
            session._prompt_single_var(spec, {})
        assert session._collected["SECRET"].value == "s3cret"

    def test_empty_required_re_prompts(self):
        session = _make_session()
        spec = EnvVarSpec(name="REQ", required=True)
        inputs = iter(["", "actual_value"])
        with patch("builtins.input", side_effect=lambda *a: next(inputs)):
            session._prompt_single_var(spec, {})
        assert session._collected["REQ"].value == "actual_value"

    def test_empty_optional_skips(self):
        session = _make_session()
        spec = EnvVarSpec(name="OPT", required=False)
        with patch("builtins.input", return_value=""):
            session._prompt_single_var(spec, {})
        assert "OPT" in session._skipped

    def test_empty_with_default(self):
        session = _make_session()
        spec = EnvVarSpec(name="DEF", required=False, default="mydefault")
        with patch("builtins.input", return_value=""):
            session._prompt_single_var(spec, {})
        assert session._collected["DEF"].value == "mydefault"

    def test_empty_with_saved_value(self):
        session = _make_session()
        spec = EnvVarSpec(name="SAVED", required=True)
        with patch("builtins.input", return_value=""):
            session._prompt_single_var(spec, {"SAVED": "saved_val"})
        assert session._collected["SAVED"].value == "saved_val"
        assert session._collected["SAVED"].from_saved is True

    def test_eof_skips(self):
        session = _make_session()
        spec = EnvVarSpec(name="MY_VAR", required=True)
        with patch("builtins.input", side_effect=EOFError):
            session._prompt_single_var(spec, {})
        assert "MY_VAR" in session._skipped

    def test_explain_with_question_mark(self):
        session = _make_session()
        spec = EnvVarSpec(name="MY_VAR", required=True)
        inputs = iter(["?", "value"])
        with patch("builtins.input", side_effect=lambda *a: next(inputs)), patch.object(session, "_explain_single_var"):
            session._prompt_single_var(spec, {})
        assert session._collected["MY_VAR"].value == "value"

    def test_hint_shows_example(self):
        session = _make_session()
        spec = EnvVarSpec(name="MY_VAR", required=False, example="example_val")
        with patch("builtins.input", return_value=""):
            session._prompt_single_var(spec, {})
        # No error means hint was printed


# ---------------------------------------------------------------------------
# _show_summary_and_confirm
# ---------------------------------------------------------------------------


class TestShowSummaryAndConfirm:
    def test_confirm(self):
        session = _make_session()
        session._collected["X"] = EnvVarValue(name="X", value="v")
        with patch("builtins.input", return_value="c"):
            result = session._show_summary_and_confirm()
        assert result is True

    def test_cancel(self):
        session = _make_session()
        session._collected["X"] = EnvVarValue(name="X", value="v")
        with patch("builtins.input", return_value="q"):
            result = session._show_summary_and_confirm()
        assert result is False

    def test_eof(self):
        session = _make_session()
        session._collected["X"] = EnvVarValue(name="X", value="v")
        with patch("builtins.input", side_effect=EOFError):
            result = session._show_summary_and_confirm()
        assert result is False

    def test_edit_re_prompts(self):
        session = _make_session()
        session._collected["X"] = EnvVarValue(name="X", value="v")
        # First "e" to edit, then "c" to confirm (after re-prompt)
        inputs = iter(["e", "c"])
        with patch("builtins.input", side_effect=lambda *a: next(inputs)), patch.object(session, "_prompt_all_vars"):
            result = session._show_summary_and_confirm()
        assert result is True

    def test_invalid_then_confirm(self):
        session = _make_session()
        session._collected["X"] = EnvVarValue(name="X", value="v")
        inputs = iter(["x", "c"])
        with patch("builtins.input", side_effect=lambda *a: next(inputs)):
            result = session._show_summary_and_confirm()
        assert result is True

    def test_sensitive_value_masked(self):
        session = _make_session()
        session._collected["X"] = EnvVarValue(name="X", value="secret", sensitive=True)
        with patch("builtins.input", return_value="c"):
            result = session._show_summary_and_confirm()
        assert result is True


# ---------------------------------------------------------------------------
# _show_explanations / _explain_single_var
# ---------------------------------------------------------------------------


class TestExplanations:
    def test_show_explanations(self):
        session = _make_session()
        with patch.object(session, "_explain_single_var"):
            session._show_explanations()

    def test_explain_with_description(self):
        session = _make_session()
        spec = EnvVarSpec(name="VAR", description="Some description", required=True, default="def", example="ex")
        session._explain_single_var(spec)

    def test_explain_with_explainer(self):
        mock_explainer = MagicMock()
        mock_explainer.explain.return_value = "LLM explanation"
        session = _make_session(explainer=mock_explainer)
        spec = EnvVarSpec(name="VAR")  # No description
        session._explain_single_var(spec)

    def test_explain_no_description_no_explainer(self):
        session = _make_session(explainer=None)
        spec = EnvVarSpec(name="VAR")
        session._explain_single_var(spec)

    def test_explain_with_explainer_returns_none(self):
        mock_explainer = MagicMock()
        mock_explainer.explain.return_value = None
        session = _make_session(explainer=mock_explainer)
        spec = EnvVarSpec(name="VAR")
        session._explain_single_var(spec)

    def test_explain_optional_var(self):
        session = _make_session()
        spec = EnvVarSpec(name="VAR", required=False)
        session._explain_single_var(spec)


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


class TestConvenienceFunctions:
    def test_prompt_env_vars(self):
        specs = _make_specs()
        mock_session = MagicMock()
        mock_session.run.return_value = EnvVarPromptResult(image="postgres:15")
        with (
            patch("elle.cli.docker.env_prompt.DockerEnvStore") as MockStore,
            patch("elle.cli.docker.env_prompt.EnvVarPromptSession", return_value=mock_session),
        ):
            result = prompt_env_vars(specs, "postgres:15")
        assert result.image == "postgres:15"
        MockStore.return_value.close.assert_called_once()

    def test_prompt_env_vars_no_store(self):
        specs = _make_specs()
        mock_session = MagicMock()
        mock_session.run.return_value = EnvVarPromptResult(image="test")
        with patch("elle.cli.docker.env_prompt.EnvVarPromptSession", return_value=mock_session):
            result = prompt_env_vars(specs, "test", use_saved=False, save_values=False)
        assert result.image == "test"

    def test_prompt_for_image_no_specs(self):
        with patch("elle.cli.docker.env_detector.DockerEnvDetector") as MockDetector:
            MockDetector.return_value.detect_from_image.return_value = ()
            result = prompt_for_image("myimage:latest")
        assert result.image == "myimage:latest"
        assert len(result.values) == 0

    def test_prompt_for_image_with_specs(self):
        specs = _make_specs()
        with (
            patch("elle.cli.docker.env_detector.DockerEnvDetector") as MockDetector,
            patch("elle.cli.docker.env_prompt.prompt_env_vars") as mock_prompt,
        ):
            MockDetector.return_value.detect_from_image.return_value = specs
            mock_prompt.return_value = EnvVarPromptResult(image="img")
            prompt_for_image("img")
        mock_prompt.assert_called_once()

    def test_prompt_env_vars_store_close_on_exception(self):
        specs = _make_specs()
        mock_session = MagicMock()
        mock_session.run.side_effect = RuntimeError("boom")
        with (
            patch("elle.cli.docker.env_prompt.DockerEnvStore") as MockStore,
            patch("elle.cli.docker.env_prompt.EnvVarPromptSession", return_value=mock_session),
        ):
            with pytest.raises(RuntimeError):
                prompt_env_vars(specs, "test")
        MockStore.return_value.close.assert_called_once()
