"""Tests for the fixit renderer module."""

from __future__ import annotations

from types import SimpleNamespace

from elle.cli.fixit.renderer import (
    RISK_COLORS,
    _render_alternative,
    _render_diagnosis,
    _render_errors,
    _render_footer,
    _render_header,
    _render_learn_more,
    _render_single_suggestion,
    _render_suggestions,
    render_action_result,
    render_compact_result,
    render_fixit_result,
    render_outcome_prompt,
)
from elle.cli.terminal.renderer import Colors


# ---------------------------------------------------------------------------
# Helpers for building mock objects
# ---------------------------------------------------------------------------


def _make_failure(
    command: str = "apt-get install foo",
    exit_code: int = 1,
    stdout: str = "",
    stderr: str = "E: Unable to locate package foo",
):
    return SimpleNamespace(
        command=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def _make_diagnosis(
    error_category: str = "command_not_found",
    summary: str = "Package not found",
    root_cause: str = "The package foo is not available",
    confidence: float = 0.8,
):
    return SimpleNamespace(
        error_category=error_category,
        summary=summary,
        root_cause=root_cause,
        confidence=confidence,
    )


def _make_fix_command(
    command: str = "apt-get update && apt-get install foo",
    explanation: str = "Update package list and retry",
    risk_level: str = "safe",
    requires_privilege: bool = False,
    verification: str | None = "dpkg -l foo",
):
    return SimpleNamespace(
        command=command,
        explanation=explanation,
        risk_level=risk_level,
        requires_privilege=requires_privilege,
        verification=verification,
    )


def _make_verification_result(
    command: str = "apt-get update",
    warnings: tuple[str, ...] = (),
):
    return SimpleNamespace(
        command=command,
        warnings=warnings,
    )


def _make_verified_fix(
    fix=None,
    verification=None,
    is_safe: bool = True,
):
    return SimpleNamespace(
        fix=fix or _make_fix_command(),
        verification=verification or _make_verification_result(),
        is_safe=is_safe,
    )


def _make_analysis(
    diagnosis=None,
    suggestions=(),
    alternative_approach: str | None = None,
    learn_more: tuple[str, ...] = (),
):
    return SimpleNamespace(
        diagnosis=diagnosis or _make_diagnosis(),
        suggestions=suggestions,
        alternative_approach=alternative_approach,
        learn_more=learn_more,
    )


def _make_context(failure=None):
    return SimpleNamespace(
        failure=failure or _make_failure(),
    )


def _make_fixit_result(
    context=None,
    analysis=None,
    verified_suggestions=(),
    verification_errors=(),
    incident_id: str | None = None,
    used_fallback: bool = False,
):
    return SimpleNamespace(
        context=context or _make_context(),
        analysis=analysis,
        verified_suggestions=verified_suggestions,
        verification_errors=verification_errors,
        incident_id=incident_id,
        used_fallback=used_fallback,
    )


# ---------------------------------------------------------------------------
# RISK_COLORS constant
# ---------------------------------------------------------------------------


class TestRiskColors:
    def test_safe_color(self):
        assert RISK_COLORS["safe"] == Colors.GREEN

    def test_moderate_color(self):
        assert RISK_COLORS["moderate"] == Colors.YELLOW

    def test_high_color(self):
        assert RISK_COLORS["high"] == Colors.RED


# ---------------------------------------------------------------------------
# _render_header
# ---------------------------------------------------------------------------


class TestRenderHeader:
    def test_basic_header(self):
        result = _make_fixit_result()
        output = _render_header(result)
        assert "Fixit Analysis" in output
        assert "apt-get install foo" in output
        assert "1" in output  # exit code

    def test_header_with_fallback(self):
        result = _make_fixit_result(used_fallback=True)
        output = _render_header(result)
        assert "rule-based" in output

    def test_header_without_fallback(self):
        result = _make_fixit_result(used_fallback=False)
        output = _render_header(result)
        assert "rule-based" not in output


# ---------------------------------------------------------------------------
# _render_diagnosis
# ---------------------------------------------------------------------------


class TestRenderDiagnosis:
    def test_no_analysis(self):
        result = _make_fixit_result(analysis=None)
        output = _render_diagnosis(result)
        assert output == []

    def test_with_analysis(self):
        analysis = _make_analysis()
        result = _make_fixit_result(analysis=analysis)
        output = _render_diagnosis(result)
        assert len(output) > 0
        assert "80%" in output[0]  # confidence
        assert "Package not found" in output[1]

    def test_with_root_cause_different_from_summary(self):
        diag = _make_diagnosis(
            summary="Package missing",
            root_cause="Repository not configured",
        )
        analysis = _make_analysis(diagnosis=diag)
        result = _make_fixit_result(analysis=analysis)
        output = _render_diagnosis(result)
        assert any("Root cause" in line for line in output)
        assert any("Repository not configured" in line for line in output)

    def test_with_root_cause_same_as_summary(self):
        diag = _make_diagnosis(
            summary="Package not found",
            root_cause="Package not found",
        )
        analysis = _make_analysis(diagnosis=diag)
        result = _make_fixit_result(analysis=analysis)
        output = _render_diagnosis(result)
        # Root cause should not be duplicated
        root_cause_lines = [line for line in output if "Root cause" in line]
        assert len(root_cause_lines) == 0

    def test_error_category_underscore_replacement(self):
        diag = _make_diagnosis(error_category="command_not_found")
        analysis = _make_analysis(diagnosis=diag)
        result = _make_fixit_result(analysis=analysis)
        output = _render_diagnosis(result)
        assert "command not found" in output[0]  # underscores replaced


# ---------------------------------------------------------------------------
# _render_suggestions
# ---------------------------------------------------------------------------


class TestRenderSuggestions:
    def test_no_safe_suggestions(self):
        verified = _make_verified_fix(is_safe=False)
        result = _make_fixit_result(verified_suggestions=(verified,))
        output = _render_suggestions(result)
        assert any("No verified fixes" in line for line in output)

    def test_with_safe_suggestions(self):
        verified = _make_verified_fix(is_safe=True)
        result = _make_fixit_result(verified_suggestions=(verified,))
        output = _render_suggestions(result)
        assert any("[1]" in line for line in output)

    def test_multiple_safe_suggestions(self):
        v1 = _make_verified_fix(
            fix=_make_fix_command(command="cmd1", explanation="First fix"),
            is_safe=True,
        )
        v2 = _make_verified_fix(
            fix=_make_fix_command(command="cmd2", explanation="Second fix"),
            is_safe=True,
        )
        result = _make_fixit_result(verified_suggestions=(v1, v2))
        output = _render_suggestions(result)
        text = "\n".join(output)
        assert "[1]" in text
        assert "[2]" in text


# ---------------------------------------------------------------------------
# _render_single_suggestion
# ---------------------------------------------------------------------------


class TestRenderSingleSuggestion:
    def test_basic_suggestion(self):
        verified = _make_verified_fix()
        output = _render_single_suggestion(1, verified)
        text = "\n".join(output)
        assert "[1]" in text
        assert "apt-get update" in text
        assert "Risk: safe" in text

    def test_suggestion_with_privilege(self):
        fix = _make_fix_command(requires_privilege=True)
        verified = _make_verified_fix(fix=fix)
        output = _render_single_suggestion(1, verified)
        text = "\n".join(output)
        assert "privilege" in text.lower()

    def test_suggestion_with_warnings(self):
        verification = _make_verification_result(warnings=("Be careful",))
        verified = _make_verified_fix(verification=verification)
        output = _render_single_suggestion(1, verified)
        text = "\n".join(output)
        assert "Warning" in text
        assert "Be careful" in text

    def test_suggestion_with_verification_cmd(self):
        fix = _make_fix_command(verification="dpkg -l foo")
        verified = _make_verified_fix(fix=fix)
        output = _render_single_suggestion(1, verified)
        text = "\n".join(output)
        assert "Verify" in text
        assert "dpkg" in text

    def test_suggestion_without_verification_cmd(self):
        fix = _make_fix_command(verification=None)
        verified = _make_verified_fix(fix=fix)
        output = _render_single_suggestion(1, verified)
        text = "\n".join(output)
        assert "Verify" not in text

    def test_suggestion_moderate_risk(self):
        fix = _make_fix_command(risk_level="moderate")
        verified = _make_verified_fix(fix=fix)
        output = _render_single_suggestion(1, verified)
        text = "\n".join(output)
        assert "moderate" in text

    def test_suggestion_high_risk(self):
        fix = _make_fix_command(risk_level="high")
        verified = _make_verified_fix(fix=fix)
        output = _render_single_suggestion(1, verified)
        text = "\n".join(output)
        assert "high" in text

    def test_suggestion_unknown_risk(self):
        fix = _make_fix_command(risk_level="extreme")
        verified = _make_verified_fix(fix=fix)
        output = _render_single_suggestion(1, verified)
        # Should not crash on unknown risk level
        text = "\n".join(output)
        assert "extreme" in text


# ---------------------------------------------------------------------------
# _render_alternative
# ---------------------------------------------------------------------------


class TestRenderAlternative:
    def test_no_analysis(self):
        result = _make_fixit_result(analysis=None)
        assert _render_alternative(result) == []

    def test_no_alternative(self):
        analysis = _make_analysis(alternative_approach=None)
        result = _make_fixit_result(analysis=analysis)
        assert _render_alternative(result) == []

    def test_with_alternative(self):
        analysis = _make_analysis(alternative_approach="Try using snap instead")
        result = _make_fixit_result(analysis=analysis)
        output = _render_alternative(result)
        assert len(output) == 2
        assert "Alternative" in output[0]
        assert "snap" in output[1]


# ---------------------------------------------------------------------------
# _render_learn_more
# ---------------------------------------------------------------------------


class TestRenderLearnMore:
    def test_no_analysis(self):
        result = _make_fixit_result(analysis=None)
        assert _render_learn_more(result) == []

    def test_no_learn_more(self):
        analysis = _make_analysis(learn_more=())
        result = _make_fixit_result(analysis=analysis)
        assert _render_learn_more(result) == []

    def test_with_learn_more(self):
        analysis = _make_analysis(learn_more=("man apt-get", "man dpkg"))
        result = _make_fixit_result(analysis=analysis)
        output = _render_learn_more(result)
        assert "Learn more" in output[0]
        assert "man apt-get" in output[1]
        assert "man dpkg" in output[2]


# ---------------------------------------------------------------------------
# _render_errors
# ---------------------------------------------------------------------------


class TestRenderErrors:
    def test_with_errors(self):
        result = _make_fixit_result(verification_errors=("Command blocked", "Denylist match"))
        output = _render_errors(result)
        assert "Verification" in output[0]
        assert "Command blocked" in output[1]
        assert "Denylist match" in output[2]


# ---------------------------------------------------------------------------
# _render_footer
# ---------------------------------------------------------------------------


class TestRenderFooter:
    def test_with_safe_suggestions(self):
        verified = _make_verified_fix(is_safe=True)
        result = _make_fixit_result(verified_suggestions=(verified,))
        output = _render_footer(result)
        assert any("copy the command" in line for line in output)

    def test_without_safe_suggestions(self):
        verified = _make_verified_fix(is_safe=False)
        result = _make_fixit_result(verified_suggestions=(verified,))
        output = _render_footer(result)
        assert any("No verified fixes" in line for line in output)

    def test_with_incident_id(self):
        result = _make_fixit_result(incident_id="INC-12345")
        output = _render_footer(result)
        text = "\n".join(output)
        assert "INC-12345" in text

    def test_without_incident_id(self):
        result = _make_fixit_result(incident_id=None)
        output = _render_footer(result)
        text = "\n".join(output)
        assert "Incident ID" not in text


# ---------------------------------------------------------------------------
# render_fixit_result (full render)
# ---------------------------------------------------------------------------


class TestRenderFixitResult:
    def test_minimal_result(self):
        result = _make_fixit_result()
        output = render_fixit_result(result)
        assert "Fixit Analysis" in output

    def test_full_result(self):
        diag = _make_diagnosis()
        fix = _make_fix_command()
        verified = _make_verified_fix(fix=fix, is_safe=True)
        analysis = _make_analysis(
            diagnosis=diag,
            alternative_approach="Try snap",
            learn_more=("man apt",),
        )
        result = _make_fixit_result(
            analysis=analysis,
            verified_suggestions=(verified,),
            verification_errors=("Warning: something",),
            incident_id="INC-99",
        )
        output = render_fixit_result(result)
        assert "Fixit Analysis" in output
        assert "Diagnosis" in output
        assert "Suggested Fixes" in output
        assert "Alternative" in output
        assert "Learn more" in output
        assert "Verification" in output
        assert "INC-99" in output

    def test_result_with_no_analysis(self):
        result = _make_fixit_result(analysis=None)
        output = render_fixit_result(result)
        assert "Fixit Analysis" in output
        # Should not crash without analysis

    def test_result_with_fallback(self):
        result = _make_fixit_result(used_fallback=True)
        output = render_fixit_result(result)
        assert "rule-based" in output


# ---------------------------------------------------------------------------
# render_action_result
# ---------------------------------------------------------------------------


class TestRenderActionResult:
    def test_success_with_stdout(self):
        output = render_action_result(
            command="apt-get update",
            exit_code=0,
            stdout="Hit:1 http://...",
            stderr="",
            success=True,
        )
        assert "succeeded" in output
        assert "Hit:1" in output

    def test_failure_with_stderr(self):
        output = render_action_result(
            command="apt-get install foo",
            exit_code=1,
            stdout="",
            stderr="E: Unable to locate package",
            success=False,
        )
        assert "failed" in output
        assert "exit 1" in output
        assert "Unable to locate" in output

    def test_success_no_output(self):
        output = render_action_result(
            command="true",
            exit_code=0,
            stdout="",
            stderr="",
            success=True,
        )
        assert "succeeded" in output

    def test_failure_stderr_hidden_on_success(self):
        output = render_action_result(
            command="cmd",
            exit_code=0,
            stdout="",
            stderr="some warning",
            success=True,
        )
        # stderr should NOT show when success=True
        assert "some warning" not in output


# ---------------------------------------------------------------------------
# render_outcome_prompt
# ---------------------------------------------------------------------------


class TestRenderOutcomePrompt:
    def test_prompt_content(self):
        output = render_outcome_prompt()
        assert "fix the problem" in output
        assert "[y]" in output
        assert "[p]" in output
        assert "[n]" in output
        assert "[w]" in output
        assert "[s]" in output


# ---------------------------------------------------------------------------
# render_compact_result
# ---------------------------------------------------------------------------


class TestRenderCompactResult:
    def test_no_analysis(self):
        result = _make_fixit_result(analysis=None)
        output = render_compact_result(result)
        assert "Unable to analyze" in output

    def test_with_analysis_no_suggestions(self):
        analysis = _make_analysis()
        result = _make_fixit_result(analysis=analysis)
        output = render_compact_result(result)
        assert "Diagnosis" in output
        assert "Package not found" in output

    def test_with_analysis_and_suggestions(self):
        verified = _make_verified_fix(is_safe=True)
        analysis = _make_analysis()
        result = _make_fixit_result(
            analysis=analysis,
            verified_suggestions=(verified,),
        )
        output = render_compact_result(result)
        assert "1 fix suggestion" in output

    def test_with_multiple_suggestions(self):
        v1 = _make_verified_fix(is_safe=True)
        v2 = _make_verified_fix(is_safe=True)
        analysis = _make_analysis()
        result = _make_fixit_result(
            analysis=analysis,
            verified_suggestions=(v1, v2),
        )
        output = render_compact_result(result)
        assert "2 fix suggestions" in output

    def test_no_safe_suggestions_no_count(self):
        verified = _make_verified_fix(is_safe=False)
        analysis = _make_analysis()
        result = _make_fixit_result(
            analysis=analysis,
            verified_suggestions=(verified,),
        )
        output = render_compact_result(result)
        assert "fix suggestion" not in output
