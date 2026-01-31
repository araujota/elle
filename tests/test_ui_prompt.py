"""Tests for elle.cli.ui.prompt -- User prompt handling."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
