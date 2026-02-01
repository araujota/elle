"""Coverage tests for elle.cli.fixit.prompts.

Targets uncovered lines:
- L147-158: _format_man_context with snippets
- L176-217: _format_prior_art_context with prior art
- build_fixit_prompt end-to-end
- get_schema_hint return value
"""

from __future__ import annotations

from pathlib import Path

from elle.cli.fixit.models import (
    CommandFailure,
    FixitContext,
    ManSnippetContext,
    PriorArtContext,
    SuccessfulAction,
)
from elle.cli.fixit.prompts import (
    BASE_SYSTEM_PROMPT,
    FIXIT_PROMPT_TEMPLATE,
    FIXIT_RESPONSE_SCHEMA,
    _format_man_context,
    _format_prior_art_context,
    build_fixit_prompt,
    get_schema_hint,
)

# =============================================================================
# Helpers
# =============================================================================


def _failure(**kw) -> CommandFailure:
    defaults = {"command": "ls /nonexistent", "exit_code": 2, "stderr": "No such file", "cwd": Path("/home")}
    defaults.update(kw)
    return CommandFailure(**defaults)


def _context(**kw) -> FixitContext:
    return FixitContext(failure=kw.pop("failure", _failure()), **kw)


# =============================================================================
# _format_man_context (lines 147-158)
# =============================================================================


class TestFormatManContext:
    """Cover _format_man_context."""

    def test_no_snippets(self):
        """Empty snippets produce 'No relevant documentation found.'"""
        ctx = _context()
        result = _format_man_context(ctx)
        assert "No relevant documentation found" in result
        assert "DOCUMENTATION CONTEXT" in result

    def test_single_snippet_with_match_section(self):
        """Single snippet with match_section includes section header."""
        ctx = _context(
            man_snippets=(
                ManSnippetContext(
                    name="ls",
                    section="1",
                    snippet="list directory contents with various options",
                    match_section="DESCRIPTION",
                    relevance_score=0.9,
                ),
            ),
        )
        result = _format_man_context(ctx)
        assert "DOCUMENTATION CONTEXT" in result
        assert "ls(1)" in result
        assert "Section: DESCRIPTION" in result
        assert "list directory contents" in result

    def test_snippet_without_match_section(self):
        """Snippet with match_section=None omits section line."""
        ctx = _context(
            man_snippets=(
                ManSnippetContext(
                    name="grep",
                    section="1",
                    snippet="search for patterns",
                    match_section=None,
                ),
            ),
        )
        result = _format_man_context(ctx)
        assert "grep(1)" in result
        assert "Section:" not in result

    def test_long_snippet_truncated(self):
        """Snippets over 800 chars are truncated with ellipsis."""
        long_text = "x" * 1000
        ctx = _context(
            man_snippets=(
                ManSnippetContext(
                    name="cat",
                    section="1",
                    snippet=long_text,
                ),
            ),
        )
        result = _format_man_context(ctx)
        assert "..." in result
        # The snippet portion should be truncated at 800 + "..."
        # Verify the full 1000-char text is not present
        assert long_text not in result

    def test_max_three_snippets(self):
        """Only first 3 snippets are included."""
        snippets = tuple(
            ManSnippetContext(
                name=f"cmd{i}",
                section="1",
                snippet=f"snippet {i}",
            )
            for i in range(5)
        )
        ctx = _context(man_snippets=snippets)
        result = _format_man_context(ctx)
        assert "cmd0(1)" in result
        assert "cmd1(1)" in result
        assert "cmd2(1)" in result
        assert "cmd3(1)" not in result
        assert "cmd4(1)" not in result


# =============================================================================
# _format_prior_art_context (lines 176-217)
# =============================================================================


class TestFormatPriorArtContext:
    """Cover _format_prior_art_context."""

    def test_no_prior_art(self):
        """Empty prior art produces 'No similar past incidents found.'"""
        ctx = _context()
        result = _format_prior_art_context(ctx)
        assert "No similar past incidents found" in result

    def test_single_prior_art_improved(self):
        """Single prior art with improved outcome shows '+' marker."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-1",
                    title="Package install failure",
                    outcome="improved",
                    summary="Updated apt cache",
                    root_cause="stale cache",
                    score=0.85,
                    precondition_match=0.7,
                    days_ago=3,
                    trigger_command="apt install foo",
                    successful_actions=(SuccessfulAction(command="apt update"),),
                    decision={"diagnosis": {"error_category": "dependency_missing"}},
                ),
            ),
        )
        result = _format_prior_art_context(ctx)
        assert "SIMILAR PAST INCIDENTS" in result
        assert "Ranked by relevance" in result
        assert "Package install failure" in result
        assert "+improved" in result
        assert "Relevance: 0.85" in result
        assert "70%" in result
        assert "Age: 3 days ago" in result
        assert "apt install foo" in result
        assert "Updated apt cache" in result
        assert "stale cache" in result
        assert "$ apt update" in result
        assert "Diagnosed as: dependency_missing" in result
        assert "strongly consider recommending" in result

    def test_partial_outcome_marker(self):
        """Partial outcome shows '~' marker."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-2",
                    title="Partial fix",
                    outcome="partial",
                    score=0.6,
                ),
            ),
        )
        result = _format_prior_art_context(ctx)
        assert "~partial" in result

    def test_no_change_outcome_marker(self):
        """no_change outcome shows '-' marker."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-3",
                    title="No change",
                    outcome="no_change",
                ),
            ),
        )
        result = _format_prior_art_context(ctx)
        assert "-no_change" in result

    def test_worse_outcome_marker(self):
        """worse outcome shows '!' marker."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-4",
                    title="Worse",
                    outcome="worse",
                ),
            ),
        )
        result = _format_prior_art_context(ctx)
        assert "!worse" in result

    def test_unknown_outcome_marker(self):
        """Unknown outcome shows '?' marker."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-5",
                    title="Unknown",
                    outcome="skipped",
                ),
            ),
        )
        result = _format_prior_art_context(ctx)
        assert "?skipped" in result

    def test_zero_score_omits_relevance(self):
        """Score of 0 omits the relevance line."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-6",
                    title="No score",
                    outcome="improved",
                    score=0.0,
                ),
            ),
        )
        result = _format_prior_art_context(ctx)
        assert "Relevance:" not in result

    def test_zero_days_ago_omits_age(self):
        """days_ago=0 omits the age line."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-7",
                    title="Today",
                    outcome="improved",
                    days_ago=0,
                ),
            ),
        )
        result = _format_prior_art_context(ctx)
        assert "Age:" not in result

    def test_no_trigger_command_omits_line(self):
        """No trigger_command omits the 'Original failed command' line."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-8",
                    title="No trigger",
                    outcome="improved",
                ),
            ),
        )
        result = _format_prior_art_context(ctx)
        assert "Original failed command" not in result

    def test_no_summary_omits_line(self):
        """Empty summary omits the summary line."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-9",
                    title="No summary",
                    outcome="improved",
                    summary="",
                ),
            ),
        )
        result = _format_prior_art_context(ctx)
        assert "Summary:" not in result

    def test_no_root_cause_omits_line(self):
        """No root_cause omits the root cause line."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-10",
                    title="No root cause",
                    outcome="improved",
                ),
            ),
        )
        result = _format_prior_art_context(ctx)
        assert "Root Cause:" not in result

    def test_no_successful_actions_omits_section(self):
        """No successful actions omits the fix commands section."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-11",
                    title="No actions",
                    outcome="improved",
                ),
            ),
        )
        result = _format_prior_art_context(ctx)
        assert "Successful fix commands" not in result

    def test_decision_without_diagnosis_omits_diagnosed(self):
        """Decision dict without 'diagnosis' key omits 'Diagnosed as' line."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-12",
                    title="No diagnosis",
                    outcome="improved",
                    decision={"approach": "manual fix"},
                ),
            ),
        )
        result = _format_prior_art_context(ctx)
        assert "Diagnosed as" not in result

    def test_decision_diagnosis_not_dict_omits_diagnosed(self):
        """Decision with diagnosis that isn't a dict omits 'Diagnosed as'."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-13",
                    title="String diag",
                    outcome="improved",
                    decision={"diagnosis": "just a string"},
                ),
            ),
        )
        result = _format_prior_art_context(ctx)
        assert "Diagnosed as" not in result

    def test_max_three_prior_arts(self):
        """Only first 3 prior arts included."""
        arts = tuple(
            PriorArtContext(
                incident_id=f"INC-{i}",
                title=f"Incident {i}",
                outcome="improved",
            )
            for i in range(5)
        )
        ctx = _context(prior_art=arts)
        result = _format_prior_art_context(ctx)
        assert "Incident 0" in result
        assert "Incident 1" in result
        assert "Incident 2" in result
        assert "Incident 3" not in result

    def test_max_three_successful_actions(self):
        """Only first 3 successful actions shown per incident."""
        actions = tuple(SuccessfulAction(command=f"cmd_{i}") for i in range(5))
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-A",
                    title="Many actions",
                    outcome="improved",
                    successful_actions=actions,
                ),
            ),
        )
        result = _format_prior_art_context(ctx)
        assert "$ cmd_0" in result
        assert "$ cmd_1" in result
        assert "$ cmd_2" in result
        assert "$ cmd_3" not in result


# =============================================================================
# build_fixit_prompt (full integration)
# =============================================================================


class TestBuildFixitPrompt:
    """Cover build_fixit_prompt end-to-end."""

    def test_basic_prompt(self):
        """Basic prompt includes command, exit code, stderr, stdout, cwd."""
        ctx = _context(
            failure=_failure(
                command="cat /etc/shadow",
                exit_code=1,
                stderr="Permission denied",
                stdout="",
            ),
        )
        prompt = build_fixit_prompt(ctx)
        assert "cat /etc/shadow" in prompt
        assert "EXIT CODE: 1" in prompt
        assert "Permission denied" in prompt
        assert "(empty)" in prompt  # stdout is empty
        assert "DOCUMENTATION CONTEXT" in prompt
        assert "SIMILAR PAST INCIDENTS" in prompt
        assert "ANALYSIS REQUIREMENTS" in prompt

    def test_prompt_with_man_and_prior_art(self):
        """Prompt includes formatted man and prior art context."""
        ctx = _context(
            failure=_failure(),
            man_snippets=(ManSnippetContext(name="ls", section="1", snippet="list dir"),),
            prior_art=(
                PriorArtContext(
                    incident_id="INC-1",
                    title="File missing",
                    outcome="improved",
                    score=0.8,
                ),
            ),
        )
        prompt = build_fixit_prompt(ctx)
        assert "ls(1)" in prompt
        assert "File missing" in prompt

    def test_prompt_truncates_long_stderr(self):
        """Stderr longer than 2000 chars is truncated."""
        long_stderr = "e" * 5000
        ctx = _context(failure=_failure(stderr=long_stderr))
        prompt = build_fixit_prompt(ctx)
        # The full 5000-char string should not appear
        assert long_stderr not in prompt
        # But some of it should be there (first 2000 chars)
        assert "e" * 100 in prompt

    def test_prompt_truncates_long_stdout(self):
        """Stdout longer than 1000 chars is truncated."""
        long_stdout = "o" * 3000
        ctx = _context(failure=_failure(stdout=long_stdout))
        prompt = build_fixit_prompt(ctx)
        assert long_stdout not in prompt

    def test_prompt_includes_schema(self):
        """Prompt includes the JSON schema."""
        ctx = _context()
        prompt = build_fixit_prompt(ctx)
        assert "error_category" in prompt
        assert "suggestions" in prompt


# =============================================================================
# get_schema_hint
# =============================================================================


class TestGetSchemaHint:
    """Cover get_schema_hint function."""

    def test_returns_dict(self):
        """get_schema_hint returns a dict with expected keys."""
        hint = get_schema_hint()
        assert isinstance(hint, dict)
        assert "diagnosis" in hint
        assert "suggestions" in hint
        assert "alternative_approach" in hint
        assert "learn_more" in hint

    def test_diagnosis_fields(self):
        """Diagnosis section has expected fields."""
        hint = get_schema_hint()
        diag = hint["diagnosis"]
        assert "error_category" in diag
        assert "summary" in diag
        assert "root_cause" in diag
        assert "confidence" in diag

    def test_suggestions_is_list(self):
        """Suggestions is a list with expected structure."""
        hint = get_schema_hint()
        assert isinstance(hint["suggestions"], list)
        assert len(hint["suggestions"]) == 1
        suggestion = hint["suggestions"][0]
        assert "command" in suggestion
        assert "risk_level" in suggestion


# =============================================================================
# Module-level constants
# =============================================================================


class TestModuleConstants:
    """Cover that module constants are accessible."""

    def test_base_system_prompt_content(self):
        """BASE_SYSTEM_PROMPT mentions ELLE and Ollama."""
        assert "ELLE" in BASE_SYSTEM_PROMPT
        assert "Ollama" in BASE_SYSTEM_PROMPT

    def test_fixit_prompt_template_has_placeholders(self):
        """FIXIT_PROMPT_TEMPLATE has expected format placeholders."""
        assert "{command}" in FIXIT_PROMPT_TEMPLATE
        assert "{exit_code}" in FIXIT_PROMPT_TEMPLATE
        assert "{stderr}" in FIXIT_PROMPT_TEMPLATE
        assert "{man_context}" in FIXIT_PROMPT_TEMPLATE

    def test_fixit_response_schema_structure(self):
        """FIXIT_RESPONSE_SCHEMA is a valid JSON schema dict."""
        assert FIXIT_RESPONSE_SCHEMA["type"] == "object"
        assert "diagnosis" in FIXIT_RESPONSE_SCHEMA["required"]
        assert "suggestions" in FIXIT_RESPONSE_SCHEMA["required"]
