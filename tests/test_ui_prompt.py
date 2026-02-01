"""Tests for elle.cli.ui.prompt -- User prompt handling."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import FormattedText

from elle.cli.ui.prompt import (
    SLASH_COMMANDS,
    ElleCompleter,
    EllePrompt,
    create_prompt_session,
    get_prompt_text,
    get_status_bar,
)

# ---------------------------------------------------------------------------
# get_prompt_text
# ---------------------------------------------------------------------------


class TestGetPromptText:
    def test_idle_state(self):
        result = get_prompt_text(state="idle")
        assert isinstance(result, FormattedText)

    def test_success_state(self):
        result = get_prompt_text(state="success")
        assert isinstance(result, FormattedText)

    def test_warning_state(self):
        result = get_prompt_text(state="warning")
        assert isinstance(result, FormattedText)

    def test_error_state(self):
        result = get_prompt_text(state="error")
        assert isinstance(result, FormattedText)

    def test_custom_prefix(self):
        result = get_prompt_text(prefix="myprefix")
        # Verify it includes the prefix in one of the tuples
        found = any("myprefix" in text for _style, text in result)
        assert found

    def test_unknown_state_falls_back(self):
        # Should fall back to idle
        result = get_prompt_text(state="nonexistent")
        assert isinstance(result, FormattedText)


# ---------------------------------------------------------------------------
# get_status_bar
# ---------------------------------------------------------------------------


class TestGetStatusBar:
    def test_empty(self):
        result = get_status_bar()
        assert isinstance(result, FormattedText)
        assert len(list(result)) == 0

    def test_model_only(self):
        result = get_status_bar(model="qwen2.5:7b")
        parts = list(result)
        assert len(parts) > 0

    def test_daemon_running(self):
        result = get_status_bar(daemon_status="running")
        parts = list(result)
        assert any("running" in t for _s, t in parts)

    def test_daemon_stopped(self):
        result = get_status_bar(daemon_status="stopped")
        parts = list(result)
        assert any("stopped" in t for _s, t in parts)

    def test_vault_docs(self):
        result = get_status_bar(vault_docs=150)
        parts = list(result)
        assert any("150" in t for _s, t in parts)

    def test_incidents_open(self):
        result = get_status_bar(incidents_open=3)
        parts = list(result)
        assert any("3" in t for _s, t in parts)

    def test_no_incidents(self):
        result = get_status_bar(incidents_open=0)
        parts = list(result)
        # Should not contain incident text
        assert not any("incident" in t for _s, t in parts)

    def test_all_fields(self):
        result = get_status_bar(
            model="llama2",
            daemon_status="running",
            vault_docs=42,
            incidents_open=1,
        )
        parts = list(result)
        assert len(parts) > 4  # Multiple sections with separators

    def test_separators_between_fields(self):
        result = get_status_bar(model="m", daemon_status="running")
        parts = list(result)
        assert any("|" in t for _s, t in parts) or any("\u2502" in t for _s, t in parts)


# ---------------------------------------------------------------------------
# ElleCompleter
# ---------------------------------------------------------------------------


class TestElleCompleter:
    def test_init(self):
        completer = ElleCompleter()
        assert completer.slash_completer is not None

    def test_get_completions_for_slash(self):
        completer = ElleCompleter()
        doc = MagicMock()
        doc.text_before_cursor = "/he"
        event = MagicMock()

        completions = list(completer.get_completions(doc, event))
        # Should delegate to slash_completer; may or may not return results
        # depending on WordCompleter internals, but should not raise
        assert isinstance(completions, list)

    def test_get_completions_for_non_slash(self):
        completer = ElleCompleter()
        doc = MagicMock()
        doc.text_before_cursor = "some text"
        event = MagicMock()

        completions = list(completer.get_completions(doc, event))
        assert completions == []


# ---------------------------------------------------------------------------
# create_prompt_session
# ---------------------------------------------------------------------------


class TestCreatePromptSession:
    def test_defaults(self):
        session = create_prompt_session()
        assert session is not None

    def test_with_history_file(self, tmp_path):
        hist_file = tmp_path / ".elle_history"
        session = create_prompt_session(history_file=hist_file)
        assert session is not None

    def test_disable_all(self):
        session = create_prompt_session(
            enable_history_search=False,
            enable_auto_suggest=False,
            enable_completion=False,
            multiline=True,
        )
        assert session is not None


# ---------------------------------------------------------------------------
# EllePrompt
# ---------------------------------------------------------------------------


class TestEllePrompt:
    def test_init_defaults(self):
        prompt = EllePrompt()
        assert prompt._state == "idle"
        assert prompt.show_status_bar is True

    def test_set_state(self):
        prompt = EllePrompt()
        prompt.set_state("success")
        assert prompt._state == "success"

    def test_update_status_partial(self):
        prompt = EllePrompt()
        prompt.update_status(model="llama2")
        assert prompt._model == "llama2"
        assert prompt._daemon_status is None

    def test_update_status_all(self):
        prompt = EllePrompt()
        prompt.update_status(
            model="qwen",
            daemon_status="running",
            vault_docs=100,
            incidents_open=2,
        )
        assert prompt._model == "qwen"
        assert prompt._daemon_status == "running"
        assert prompt._vault_docs == 100
        assert prompt._incidents_open == 2

    def test_get_bottom_toolbar_hidden(self):
        prompt = EllePrompt(show_status_bar=False)
        assert prompt._get_bottom_toolbar() is None

    def test_get_bottom_toolbar_visible(self):
        prompt = EllePrompt()
        prompt.update_status(model="test-model")
        result = prompt._get_bottom_toolbar()
        assert isinstance(result, FormattedText)

    def test_prompt_calls_session(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "hello"
        result = prompt.prompt()
        assert result == "hello"
        prompt.session.prompt.assert_called_once()

    def test_prompt_confirm_yes(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "y"
        assert prompt.prompt_confirm("Continue?") is True

    def test_prompt_confirm_no(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "n"
        assert prompt.prompt_confirm("Continue?") is False

    def test_prompt_confirm_default_true(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = ""
        assert prompt.prompt_confirm("Continue?", default=True) is True

    def test_prompt_confirm_default_false(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = ""
        assert prompt.prompt_confirm("Continue?", default=False) is False

    def test_prompt_confirm_with_back_yes(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "yes"
        assert prompt.prompt_confirm_with_back("Continue?") is True

    def test_prompt_confirm_with_back_no(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "no"
        assert prompt.prompt_confirm_with_back("Continue?") is False

    def test_prompt_confirm_with_back_back(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "b"
        assert prompt.prompt_confirm_with_back("Continue?") == "back"

    def test_prompt_confirm_with_back_default(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = ""
        assert prompt.prompt_confirm_with_back("Continue?", default=True) is True

    def test_prompt_confirm_with_back_no_allow(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "y"
        assert prompt.prompt_confirm_with_back("Continue?", allow_back=False) is True

    def test_prompt_choice(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "a"
        result = prompt.prompt_choice("Pick:", [("a", "Alpha"), ("b", "Beta")])
        assert result == "a"

    def test_prompt_choice_default(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = ""
        result = prompt.prompt_choice("Pick:", [("a", "Alpha")], default="a")
        assert result == "a"

    def test_prompt_env_var_with_value(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "myvalue"
        value, from_saved = prompt.prompt_env_var("MY_VAR")
        assert value == "myvalue"
        assert from_saved is False

    def test_prompt_env_var_saved(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = ""
        value, from_saved = prompt.prompt_env_var("MY_VAR", saved_value="saved_val")
        assert value == "saved_val"
        assert from_saved is True

    def test_prompt_env_var_default(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = ""
        value, from_saved = prompt.prompt_env_var("MY_VAR", default="default_val")
        assert value == "default_val"
        assert from_saved is False

    def test_prompt_env_var_sensitive(self):
        prompt = EllePrompt()
        with patch("getpass.getpass", return_value="secret"):
            value, from_saved = prompt.prompt_env_var("MY_SECRET", sensitive=True)
        assert value == "secret"
        assert from_saved is False

    def test_prompt_env_var_with_hint(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "val"
        value, from_saved = prompt.prompt_env_var("MY_VAR", hint="Enter a value")
        assert value == "val"

    def test_prompt_env_var_batch(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "val"

        specs = [
            {"name": "REQ_VAR", "required": True},
            {"name": "OPT_VAR", "required": False},
        ]
        results = prompt.prompt_env_var_batch(specs)
        assert "REQ_VAR" in results
        assert "OPT_VAR" in results

    def test_prompt_env_var_batch_interrupt(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.side_effect = KeyboardInterrupt()

        specs = [{"name": "VAR1", "required": True}]
        results = prompt.prompt_env_var_batch(specs)
        assert results == {}

    def test_prompt_select_empty(self):
        prompt = EllePrompt()
        assert prompt.prompt_select("Pick:", []) is None

    def test_prompt_multi_select_empty(self):
        prompt = EllePrompt()
        assert prompt.prompt_multi_select("Pick:", []) is None


# ---------------------------------------------------------------------------
# SLASH_COMMANDS
# ---------------------------------------------------------------------------


class TestSlashCommands:
    def test_has_commands(self):
        assert len(SLASH_COMMANDS) > 0
        for cmd, desc in SLASH_COMMANDS:
            assert cmd.startswith("/")
            assert len(desc) > 0


# ---------------------------------------------------------------------------
# get_status_bar - cwd parameter (currently skipped in impl)
# ---------------------------------------------------------------------------


class TestGetStatusBarCwd:
    def test_with_cwd_does_not_error(self):
        result = get_status_bar(cwd="/home/user")
        assert isinstance(result, FormattedText)

    def test_all_fields_with_cwd(self):
        result = get_status_bar(
            model="test",
            daemon_status="running",
            vault_docs=10,
            incidents_open=1,
            cwd="/tmp",
        )
        parts = list(result)
        assert len(parts) > 0


# ---------------------------------------------------------------------------
# ElleCompleter - edge cases
# ---------------------------------------------------------------------------


class TestElleCompleterEdge:
    def test_completions_for_exact_command(self):
        completer = ElleCompleter()
        doc = MagicMock()
        doc.text_before_cursor = "/help"
        event = MagicMock()
        completions = list(completer.get_completions(doc, event))
        assert isinstance(completions, list)

    def test_completions_for_slash_only(self):
        completer = ElleCompleter()
        doc = MagicMock()
        doc.text_before_cursor = "/"
        event = MagicMock()
        completions = list(completer.get_completions(doc, event))
        assert isinstance(completions, list)
        # Should return all slash commands
        assert len(completions) >= len(SLASH_COMMANDS)

    def test_completions_for_empty_string(self):
        completer = ElleCompleter()
        doc = MagicMock()
        doc.text_before_cursor = ""
        event = MagicMock()
        completions = list(completer.get_completions(doc, event))
        assert completions == []


# ---------------------------------------------------------------------------
# get_prompt_text - edge cases
# ---------------------------------------------------------------------------


class TestGetPromptTextEdge:
    def test_output_contains_prefix(self):
        result = get_prompt_text(prefix="test")
        text = "".join(t for _, t in result)
        assert "test " in text

    def test_idle_indicator_icon(self):
        from elle.cli.ui.theme import Icons

        result = get_prompt_text(state="idle")
        text = "".join(t for _, t in result)
        assert Icons.PROMPT_IDLE in text

    def test_success_indicator_icon(self):
        from elle.cli.ui.theme import Icons

        result = get_prompt_text(state="success")
        text = "".join(t for _, t in result)
        assert Icons.PROMPT_SUCCESS in text

    def test_warning_indicator_icon(self):
        from elle.cli.ui.theme import Icons

        result = get_prompt_text(state="warning")
        text = "".join(t for _, t in result)
        assert Icons.PROMPT_WARNING in text

    def test_error_indicator_icon(self):
        from elle.cli.ui.theme import Icons

        result = get_prompt_text(state="error")
        text = "".join(t for _, t in result)
        assert Icons.PROMPT_ERROR in text


# ---------------------------------------------------------------------------
# EllePrompt - prompt_select with options
# ---------------------------------------------------------------------------


class TestPromptSelectWithOptions:
    def test_prompt_select_with_app(self):
        """Test prompt_select returns a result from the Application."""
        prompt = EllePrompt()
        options = [
            ("a", "Alpha", "First option"),
            ("b", "Beta", "Second option"),
        ]
        with patch("elle.cli.ui.prompt.Application") as MockApp:
            mock_app = MagicMock()
            mock_app.run.return_value = "a"
            MockApp.return_value = mock_app
            result = prompt.prompt_select("Pick:", options)
            assert result == "a"

    def test_prompt_select_cancelled(self):
        prompt = EllePrompt()
        options = [("a", "Alpha", "First")]
        with patch("elle.cli.ui.prompt.Application") as MockApp:
            mock_app = MagicMock()
            mock_app.run.side_effect = KeyboardInterrupt()
            MockApp.return_value = mock_app
            result = prompt.prompt_select("Pick:", options)
            assert result is None

    def test_prompt_select_eof(self):
        prompt = EllePrompt()
        options = [("a", "Alpha", "First")]
        with patch("elle.cli.ui.prompt.Application") as MockApp:
            mock_app = MagicMock()
            mock_app.run.side_effect = EOFError()
            MockApp.return_value = mock_app
            result = prompt.prompt_select("Pick:", options)
            assert result is None

    def test_prompt_select_default_index(self):
        prompt = EllePrompt()
        options = [("a", "Alpha", "First"), ("b", "Beta", "Second")]
        with patch("elle.cli.ui.prompt.Application") as MockApp:
            mock_app = MagicMock()
            mock_app.run.return_value = "b"
            MockApp.return_value = mock_app
            result = prompt.prompt_select("Pick:", options, default_index=1)
            assert result == "b"


# ---------------------------------------------------------------------------
# EllePrompt - prompt_multi_select with options
# ---------------------------------------------------------------------------


class TestPromptMultiSelectWithOptions:
    def test_prompt_multi_select_with_app(self):
        prompt = EllePrompt()
        options = [
            ("a", "Alpha", "First", True),
            ("b", "Beta", "Second", False),
        ]
        with patch("elle.cli.ui.prompt.Application") as MockApp:
            mock_app = MagicMock()
            mock_app.run.return_value = ["a"]
            MockApp.return_value = mock_app
            result = prompt.prompt_multi_select("Pick:", options)
            assert result == ["a"]

    def test_prompt_multi_select_cancelled(self):
        prompt = EllePrompt()
        options = [("a", "Alpha", "First", True)]
        with patch("elle.cli.ui.prompt.Application") as MockApp:
            mock_app = MagicMock()
            mock_app.run.side_effect = KeyboardInterrupt()
            MockApp.return_value = mock_app
            result = prompt.prompt_multi_select("Pick:", options)
            assert result is None

    def test_prompt_multi_select_eof(self):
        prompt = EllePrompt()
        options = [("a", "Alpha", "First", False)]
        with patch("elle.cli.ui.prompt.Application") as MockApp:
            mock_app = MagicMock()
            mock_app.run.side_effect = EOFError()
            MockApp.return_value = mock_app
            result = prompt.prompt_multi_select("Pick:", options)
            assert result is None


# ---------------------------------------------------------------------------
# EllePrompt - prompt_env_var edge cases
# ---------------------------------------------------------------------------


class TestPromptEnvVarEdge:
    def test_required_empty_keeps_prompting(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        # First empty, then a value
        prompt.session.prompt.side_effect = ["", "final"]
        value, from_saved = prompt.prompt_env_var("MY_VAR", required=True)
        assert value == "final"
        assert from_saved is False
        assert prompt.session.prompt.call_count == 2

    def test_not_required_empty_returns_empty(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = ""
        value, from_saved = prompt.prompt_env_var("MY_VAR", required=False)
        assert value == ""
        assert from_saved is False

    def test_sensitive_with_saved_value(self):
        """When sensitive with saved value and empty input, uses saved."""
        prompt = EllePrompt()
        with patch("getpass.getpass", return_value=""):
            value, from_saved = prompt.prompt_env_var("MY_SECRET", sensitive=True, saved_value="old_secret")
        assert value == "old_secret"
        assert from_saved is True

    def test_sensitive_with_new_value(self):
        prompt = EllePrompt()
        with patch("getpass.getpass", return_value="new_secret"):
            value, from_saved = prompt.prompt_env_var("MY_SECRET", sensitive=True)
        assert value == "new_secret"
        assert from_saved is False

    def test_hint_prints(self, capsys):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "val"
        prompt.prompt_env_var("MY_VAR", hint="Enter something special")
        captured = capsys.readouterr()
        assert "Enter something special" in captured.out

    def test_saved_value_prints_when_no_hint(self, capsys):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = ""
        prompt.prompt_env_var("MY_VAR", saved_value="existing_val")
        captured = capsys.readouterr()
        assert "existing_val" in captured.out

    def test_default_prints_when_no_hint_no_saved(self, capsys):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = ""
        prompt.prompt_env_var("MY_VAR", default="fallback_val")
        captured = capsys.readouterr()
        assert "fallback_val" in captured.out

    def test_sensitive_saved_value_masked(self, capsys):
        prompt = EllePrompt()
        with patch("getpass.getpass", return_value=""):
            prompt.prompt_env_var("SECRET", sensitive=True, saved_value="hidden")
        captured = capsys.readouterr()
        assert "********" in captured.out
        assert "hidden" not in captured.out


# ---------------------------------------------------------------------------
# EllePrompt - prompt_env_var_batch edge cases
# ---------------------------------------------------------------------------


class TestPromptEnvVarBatchEdge:
    def test_mixed_required_and_optional(self, capsys):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "val"

        specs = [
            {"name": "REQ1", "required": True},
            {"name": "REQ2", "required": True},
            {"name": "OPT1", "required": False},
        ]
        results = prompt.prompt_env_var_batch(specs)
        assert "REQ1" in results
        assert "REQ2" in results
        assert "OPT1" in results
        captured = capsys.readouterr()
        assert "Optional variables" in captured.out

    def test_optional_interrupt(self):
        """Interrupting during optional prompts returns only required results."""
        prompt = EllePrompt()
        prompt.session = MagicMock()
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "required_val"
            raise KeyboardInterrupt()

        prompt.session.prompt.side_effect = side_effect

        specs = [
            {"name": "REQ1", "required": True},
            {"name": "OPT1", "required": False},
        ]
        results = prompt.prompt_env_var_batch(specs)
        assert "REQ1" in results
        assert "OPT1" not in results

    def test_with_saved_values(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = ""

        specs = [{"name": "VAR1", "required": True}]
        saved = {"VAR1": "saved_val"}
        results = prompt.prompt_env_var_batch(specs, saved_values=saved)
        assert results["VAR1"] == ("saved_val", True)

    def test_empty_value_not_stored(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = ""

        specs = [{"name": "OPT1", "required": False}]
        results = prompt.prompt_env_var_batch(specs)
        assert "OPT1" not in results  # Empty value is not stored

    def test_sensitive_batch(self):
        prompt = EllePrompt()
        with patch("getpass.getpass", return_value="secret_val"):
            specs = [{"name": "SECRET", "required": True, "sensitive": True}]
            results = prompt.prompt_env_var_batch(specs)
        assert results["SECRET"] == ("secret_val", False)

    def test_batch_with_descriptions(self, capsys):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "val"

        specs = [
            {"name": "VAR1", "required": True, "description": "This is var1"},
        ]
        prompt.prompt_env_var_batch(specs)
        captured = capsys.readouterr()
        assert "This is var1" in captured.out


# ---------------------------------------------------------------------------
# EllePrompt - prompt_choice edge cases
# ---------------------------------------------------------------------------


class TestPromptChoiceEdge:
    def test_invalid_then_valid_choice(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.side_effect = ["invalid", "a"]
        result = prompt.prompt_choice("Pick:", [("a", "Alpha"), ("b", "Beta")])
        assert result == "a"
        assert prompt.session.prompt.call_count == 2

    def test_case_insensitive_choice(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "A"
        result = prompt.prompt_choice("Pick:", [("a", "Alpha"), ("b", "Beta")])
        assert result == "a"

    def test_empty_with_no_default(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        # Empty first, then valid
        prompt.session.prompt.side_effect = ["", "b"]
        result = prompt.prompt_choice("Pick:", [("a", "Alpha"), ("b", "Beta")])
        assert result == "b"


# ---------------------------------------------------------------------------
# EllePrompt - prompt_confirm edge cases
# ---------------------------------------------------------------------------


class TestPromptConfirmEdge:
    def test_invalid_input_loops(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.side_effect = ["invalid", "maybe", "y"]
        result = prompt.prompt_confirm("Continue?")
        assert result is True
        assert prompt.session.prompt.call_count == 3

    def test_yes_full_word(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "yes"
        assert prompt.prompt_confirm("Continue?") is True

    def test_no_full_word(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "no"
        assert prompt.prompt_confirm("Continue?") is False

    def test_whitespace_trimmed(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "  y  "
        assert prompt.prompt_confirm("Continue?") is True

    def test_case_insensitive(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "Y"
        assert prompt.prompt_confirm("Continue?") is True


# ---------------------------------------------------------------------------
# EllePrompt - prompt_confirm_with_back edge cases
# ---------------------------------------------------------------------------


class TestPromptConfirmWithBackEdge:
    def test_back_full_word(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.return_value = "back"
        assert prompt.prompt_confirm_with_back("Continue?") == "back"

    def test_invalid_loops(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        prompt.session.prompt.side_effect = ["invalid", "y"]
        result = prompt.prompt_confirm_with_back("Continue?")
        assert result is True
        assert prompt.session.prompt.call_count == 2

    def test_allow_back_false_ignores_b(self):
        prompt = EllePrompt()
        prompt.session = MagicMock()
        # "b" is not valid when allow_back=False, loops until "y"
        prompt.session.prompt.side_effect = ["b", "y"]
        result = prompt.prompt_confirm_with_back("Continue?", allow_back=False)
        assert result is True
        assert prompt.session.prompt.call_count == 2


# ---------------------------------------------------------------------------
# create_prompt_session - additional tests
# ---------------------------------------------------------------------------


class TestCreatePromptSessionEdge:
    def test_with_all_options(self):
        session = create_prompt_session(
            enable_history_search=True,
            enable_auto_suggest=True,
            enable_completion=True,
            multiline=False,
        )
        assert session is not None
        # Session should have completer
        assert session.completer is not None

    def test_no_completer(self):
        session = create_prompt_session(enable_completion=False)
        assert session.completer is None

    def test_no_auto_suggest(self):
        session = create_prompt_session(enable_auto_suggest=False)
        assert session.auto_suggest is None

    def test_with_auto_suggest(self):
        session = create_prompt_session(enable_auto_suggest=True)
        assert isinstance(session.auto_suggest, AutoSuggestFromHistory)

    def test_multiline_mode(self):
        session = create_prompt_session(multiline=True)
        assert session.default_buffer.multiline() is True


# ---------------------------------------------------------------------------
# PROMPT_STYLE constant
# ---------------------------------------------------------------------------


class TestPromptStyle:
    def test_prompt_style_exists(self):
        from elle.cli.ui.prompt import PROMPT_STYLE

        assert PROMPT_STYLE is not None
