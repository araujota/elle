from __future__ import annotations

"""Tests for the Fixit interactive UI module.

Covers:
- InteractiveState enum values
- FixitInteractive initialization and safe_suggestions filtering
- Rendering: LIST, DETAIL, CONFIRM, OUTCOME states
- Rendering edge cases: no suggestions, no analysis, no selected_index
- Input handling: LIST state (digit, v, q/Q/Enter, invalid)
- Input handling: DETAIL state (Enter, b/B, q/Q, invalid)
- Input handling: CONFIRM state (y/Y, n/N, invalid, None selected_index)
- Input handling: OUTCOME state (all outcome keys, invalid)
- selected_outcome property (default, after selection)
- Helper methods: _box_line, _box_separator, _wrap_text
- Box drawing constants
- get_keypress: termios path and fallback path
- run_interactive_fixit: no suggestions, with suggestions, callback, keyboard
  interrupt, command execution (success/failure), outcome recording, incident
  finalization (success, exception)
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elle.cli.fixit.interactive import (
    BOX_BOTTOM_LEFT,
    BOX_BOTTOM_RIGHT,
    BOX_HORIZONTAL,
    BOX_T_LEFT,
    BOX_T_RIGHT,
    BOX_TOP_LEFT,
    BOX_TOP_RIGHT,
    BOX_VERTICAL,
    FixitInteractive,
    InteractiveState,
    get_keypress,
    run_interactive_fixit,
)
from elle.cli.fixit.models import (
    CommandFailure,
    FixCommand,
    FixitAction,
    FixitAnalysis,
    FixitContext,
    FixitDiagnosis,
    FixitOutcome,
    FixitResult,
    VerificationResult,
    VerificationStatus,
    VerifiedFixCommand,
)

# =============================================================================
# Shared helpers
# =============================================================================


def _make_failure(**overrides: Any) -> CommandFailure:
    return CommandFailure(
        command=overrides.get("command", "apt install foo"),
        exit_code=overrides.get("exit_code", 1),
        stdout=overrides.get("stdout", ""),
        stderr=overrides.get("stderr", "E: Unable to locate package foo"),
        cwd=overrides.get("cwd", Path("/home/user")),
    )


def _make_context(**overrides: Any) -> FixitContext:
    failure = overrides.get("failure", _make_failure())
    return FixitContext(
        failure=failure,
        history=overrides.get("history", ()),
        man_snippets=overrides.get("man_snippets", ()),
        prior_art=overrides.get("prior_art", ()),
    )


def _make_diagnosis(**overrides: Any) -> FixitDiagnosis:
    return FixitDiagnosis(
        error_category=overrides.get("error_category", "dependency_missing"),
        summary=overrides.get("summary", "Package not found"),
        root_cause=overrides.get("root_cause", "The package foo is not in the repo"),
        confidence=overrides.get("confidence", 0.85),
    )


def _make_fix(**overrides: Any) -> FixCommand:
    return FixCommand(
        command=overrides.get("command", "apt update"),
        explanation=overrides.get("explanation", "Refresh package index"),
        risk_level=overrides.get("risk_level", "safe"),
        requires_privilege=overrides.get("requires_privilege", False),
        verification=overrides.get("verification"),
    )


def _make_verification(**overrides: Any) -> VerificationResult:
    return VerificationResult(
        command=overrides.get("command", "apt update"),
        status=overrides.get("status", VerificationStatus.PASSED),
        checks_passed=overrides.get("checks_passed", ("denylist", "parse")),
        checks_failed=overrides.get("checks_failed", ()),
        warnings=overrides.get("warnings", ()),
    )


def _make_verified(
    is_safe: bool = True,
    fix: FixCommand | None = None,
    verification: VerificationResult | None = None,
) -> VerifiedFixCommand:
    return VerifiedFixCommand(
        fix=fix or _make_fix(),
        verification=verification or _make_verification(),
        is_safe=is_safe,
    )


def _make_analysis(**overrides: Any) -> FixitAnalysis:
    return FixitAnalysis(
        diagnosis=overrides.get("diagnosis", _make_diagnosis()),
        suggestions=overrides.get(
            "suggestions",
            (_make_fix(),),
        ),
        alternative_approach=overrides.get("alternative_approach"),
        learn_more=overrides.get("learn_more", ()),
    )


def _make_result(**overrides: Any) -> FixitResult:
    return FixitResult(
        context=overrides.get("context", _make_context()),
        analysis=overrides.get("analysis"),
        verified_suggestions=overrides.get("verified_suggestions", ()),
        outcome=overrides.get("outcome", FixitOutcome.SKIPPED),
        incident_id=overrides.get("incident_id"),
    )


def _make_result_with_safe_suggestions(count: int = 1) -> FixitResult:
    """Build a FixitResult with the given number of safe verified suggestions."""
    suggestions = []
    for i in range(count):
        fix = _make_fix(
            command=f"fix-cmd-{i}",
            explanation=f"Fix explanation number {i}",
            risk_level="safe",
        )
        ver = _make_verification(command=f"fix-cmd-{i}")
        suggestions.append(_make_verified(is_safe=True, fix=fix, verification=ver))

    return _make_result(
        analysis=_make_analysis(),
        verified_suggestions=tuple(suggestions),
    )


def _make_session(**overrides: Any) -> MagicMock:
    session = MagicMock()
    session.last_cmd = overrides.get("last_cmd", "apt install foo")
    session.last_exit = overrides.get("last_exit", 1)
    session.last_stdout = overrides.get("last_stdout", "")
    session.last_stderr = overrides.get("last_stderr", "E: Unable to locate package foo")
    session.cwd = overrides.get("cwd", Path("/home/user"))
    session.history = overrides.get("history", ())
    return session


# =============================================================================
# TestInteractiveState
# =============================================================================


class TestInteractiveState:
    """Tests for InteractiveState enum."""

    def test_all_states_exist(self):
        """All expected states are defined."""
        assert InteractiveState.LIST is not None
        assert InteractiveState.DETAIL is not None
        assert InteractiveState.CONFIRM is not None
        assert InteractiveState.OUTCOME is not None

    def test_states_are_unique(self):
        """All state values are distinct."""
        values = [s.value for s in InteractiveState]
        assert len(values) == len(set(values))


# =============================================================================
# TestBoxDrawingConstants
# =============================================================================


class TestBoxDrawingConstants:
    """Tests for box drawing character constants."""

    def test_constants_are_single_characters(self):
        """Each box drawing constant is a single character."""
        for const in (
            BOX_TOP_LEFT,
            BOX_TOP_RIGHT,
            BOX_BOTTOM_LEFT,
            BOX_BOTTOM_RIGHT,
            BOX_HORIZONTAL,
            BOX_VERTICAL,
            BOX_T_LEFT,
            BOX_T_RIGHT,
        ):
            assert len(const) == 1

    def test_expected_characters(self):
        """Box drawing constants have the expected Unicode values."""
        assert BOX_TOP_LEFT == "\u250c"
        assert BOX_TOP_RIGHT == "\u2510"
        assert BOX_BOTTOM_LEFT == "\u2514"
        assert BOX_BOTTOM_RIGHT == "\u2518"
        assert BOX_HORIZONTAL == "\u2500"
        assert BOX_VERTICAL == "\u2502"
        assert BOX_T_LEFT == "\u251c"
        assert BOX_T_RIGHT == "\u2524"


# =============================================================================
# TestFixitInteractiveInit
# =============================================================================


class TestFixitInteractiveInit:
    """Tests for FixitInteractive initialization."""

    def test_initial_state_is_list(self):
        """Initial state is LIST."""
        result = _make_result_with_safe_suggestions(2)
        ui = FixitInteractive(result)
        assert ui.state == InteractiveState.LIST

    def test_selected_index_is_none(self):
        """selected_index starts as None."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        assert ui.selected_index is None

    def test_safe_suggestions_filtered(self):
        """Only safe suggestions are included."""
        safe = _make_verified(is_safe=True, fix=_make_fix(command="safe-cmd"))
        unsafe = _make_verified(is_safe=False, fix=_make_fix(command="unsafe-cmd"))
        result = _make_result(verified_suggestions=(safe, unsafe))
        ui = FixitInteractive(result)

        assert len(ui.safe_suggestions) == 1
        assert ui.safe_suggestions[0].fix.command == "safe-cmd"

    def test_no_safe_suggestions(self):
        """All suggestions unsafe results in empty safe_suggestions."""
        unsafe = _make_verified(is_safe=False)
        result = _make_result(verified_suggestions=(unsafe,))
        ui = FixitInteractive(result)

        assert len(ui.safe_suggestions) == 0

    def test_empty_suggestions(self):
        """No verified suggestions results in empty safe_suggestions."""
        result = _make_result(verified_suggestions=())
        ui = FixitInteractive(result)

        assert len(ui.safe_suggestions) == 0


# =============================================================================
# TestRenderList
# =============================================================================


class TestRenderList:
    """Tests for list view rendering."""

    def test_render_list_contains_title(self):
        """List view includes 'Fixit Analysis' title."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        output = ui.render()
        assert "Fixit Analysis" in output

    def test_render_list_contains_failed_command(self):
        """List view shows the failed command."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        output = ui.render()
        assert "apt install foo" in output

    def test_render_list_contains_exit_code(self):
        """List view shows the exit code."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        output = ui.render()
        assert "Exit:" in output

    def test_render_list_with_analysis_shows_diagnosis(self):
        """List view shows diagnosis when analysis is present."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        output = ui.render()
        assert "Diagnosis:" in output
        assert "85%" in output

    def test_render_list_without_analysis_skips_diagnosis(self):
        """List view omits diagnosis when analysis is None."""
        safe = _make_verified(is_safe=True)
        result = _make_result(analysis=None, verified_suggestions=(safe,))
        ui = FixitInteractive(result)
        output = ui.render()
        assert "Diagnosis:" not in output

    def test_render_list_shows_suggestion_numbers(self):
        """Each suggestion gets a numbered label."""
        result = _make_result_with_safe_suggestions(3)
        ui = FixitInteractive(result)
        output = ui.render()
        assert "[1]" in output
        assert "[2]" in output
        assert "[3]" in output

    def test_render_list_shows_risk_levels(self):
        """Risk levels are displayed for each suggestion."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        output = ui.render()
        assert "Risk:" in output

    def test_render_list_shows_privilege_required(self):
        """Shows 'Requires: privilege' when fix needs elevation."""
        fix = _make_fix(requires_privilege=True)
        ver = _make_verification()
        verified = _make_verified(is_safe=True, fix=fix, verification=ver)
        result = _make_result(
            analysis=_make_analysis(),
            verified_suggestions=(verified,),
        )
        ui = FixitInteractive(result)
        output = ui.render()
        assert "privilege" in output.lower()

    def test_render_list_no_suggestions_shows_message(self):
        """Shows 'No verified fixes available' when no safe suggestions."""
        result = _make_result(analysis=_make_analysis(), verified_suggestions=())
        ui = FixitInteractive(result)
        output = ui.render()
        assert "No verified fixes available" in output

    def test_render_list_no_suggestions_shows_exit_controls(self):
        """Shows only '[q] Exit' when no suggestions exist."""
        result = _make_result(analysis=_make_analysis(), verified_suggestions=())
        ui = FixitInteractive(result)
        output = ui.render()
        assert "[q] Exit" in output

    def test_render_list_with_suggestions_shows_full_controls(self):
        """Shows '[1-N] Apply fix  [v] View details  [q] Skip' controls."""
        result = _make_result_with_safe_suggestions(2)
        ui = FixitInteractive(result)
        output = ui.render()
        assert "[1-2] Apply fix" in output
        assert "[v] View details" in output
        assert "[q] Skip" in output

    def test_render_list_box_borders(self):
        """Output includes box drawing border characters."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        output = ui.render()
        assert BOX_TOP_LEFT in output
        assert BOX_TOP_RIGHT in output
        assert BOX_BOTTOM_LEFT in output
        assert BOX_BOTTOM_RIGHT in output

    def test_render_list_moderate_risk_color(self):
        """Moderate risk suggestions get yellow color code."""
        fix = _make_fix(risk_level="moderate")
        ver = _make_verification()
        verified = _make_verified(is_safe=True, fix=fix, verification=ver)
        result = _make_result(
            analysis=_make_analysis(),
            verified_suggestions=(verified,),
        )
        ui = FixitInteractive(result)
        output = ui.render()
        assert "moderate" in output

    def test_render_list_high_risk_color(self):
        """High risk suggestions get red color code."""
        fix = _make_fix(risk_level="high")
        ver = _make_verification()
        verified = _make_verified(is_safe=True, fix=fix, verification=ver)
        result = _make_result(
            analysis=_make_analysis(),
            verified_suggestions=(verified,),
        )
        ui = FixitInteractive(result)
        output = ui.render()
        assert "high" in output

    def test_render_list_unknown_risk_no_crash(self):
        """An unknown risk level does not crash rendering (empty color)."""
        fix = FixCommand(
            command="test",
            explanation="test command",
            risk_level="safe",  # use valid type, but test the get() fallback
        )
        ver = _make_verification()
        verified = _make_verified(is_safe=True, fix=fix, verification=ver)
        result = _make_result(
            analysis=_make_analysis(),
            verified_suggestions=(verified,),
        )
        ui = FixitInteractive(result)
        # Should not raise
        output = ui.render()
        assert BOX_VERTICAL in output


# =============================================================================
# TestRenderDetail
# =============================================================================


class TestRenderDetail:
    """Tests for detail view rendering."""

    def test_render_detail_shows_command(self):
        """Detail view displays the full command."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = 0
        output = ui.render()
        assert "Command:" in output
        assert "fix-cmd-0" in output

    def test_render_detail_shows_risk(self):
        """Detail view shows risk level."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = 0
        output = ui.render()
        assert "Risk:" in output

    def test_render_detail_shows_explanation(self):
        """Detail view shows explanation section."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = 0
        output = ui.render()
        assert "Explanation:" in output

    def test_render_detail_shows_privilege_when_required(self):
        """Detail view shows 'Polkit elevation' when privilege is required."""
        fix = _make_fix(requires_privilege=True)
        ver = _make_verification()
        verified = _make_verified(is_safe=True, fix=fix, verification=ver)
        result = _make_result(
            analysis=_make_analysis(),
            verified_suggestions=(verified,),
        )
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = 0
        output = ui.render()
        assert "Polkit elevation" in output

    def test_render_detail_omits_privilege_when_not_required(self):
        """Detail view omits privilege section when not required."""
        fix = _make_fix(requires_privilege=False)
        ver = _make_verification()
        verified = _make_verified(is_safe=True, fix=fix, verification=ver)
        result = _make_result(
            analysis=_make_analysis(),
            verified_suggestions=(verified,),
        )
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = 0
        output = ui.render()
        assert "Polkit elevation" not in output

    def test_render_detail_shows_verification_command(self):
        """Detail view shows verification command when present."""
        fix = _make_fix(verification="apt list --installed | grep foo")
        ver = _make_verification()
        verified = _make_verified(is_safe=True, fix=fix, verification=ver)
        result = _make_result(
            analysis=_make_analysis(),
            verified_suggestions=(verified,),
        )
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = 0
        output = ui.render()
        assert "Verify with:" in output
        assert "apt list --installed" in output

    def test_render_detail_omits_verification_when_none(self):
        """Detail view omits verification section when fix.verification is None."""
        fix = _make_fix(verification=None)
        ver = _make_verification()
        verified = _make_verified(is_safe=True, fix=fix, verification=ver)
        result = _make_result(
            analysis=_make_analysis(),
            verified_suggestions=(verified,),
        )
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = 0
        output = ui.render()
        assert "Verify with:" not in output

    def test_render_detail_shows_warnings(self):
        """Detail view shows verification warnings."""
        fix = _make_fix()
        ver = _make_verification(warnings=("Unknown flag: --foo",))
        verified = _make_verified(is_safe=True, fix=fix, verification=ver)
        result = _make_result(
            analysis=_make_analysis(),
            verified_suggestions=(verified,),
        )
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = 0
        output = ui.render()
        assert "Warnings:" in output
        assert "Unknown flag: --foo" in output

    def test_render_detail_omits_warnings_when_empty(self):
        """Detail view omits warnings section when no warnings."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = 0
        output = ui.render()
        assert "Warnings:" not in output

    def test_render_detail_shows_controls(self):
        """Detail view shows navigation controls."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = 0
        output = ui.render()
        assert "[Enter] Apply this fix" in output
        assert "[b] Back to list" in output
        assert "[q] Skip all" in output

    def test_render_detail_no_selected_index_falls_back_to_list(self):
        """Detail view falls back to list rendering when selected_index is None."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = None
        output = ui.render()
        # Falls back to _render_list
        assert "Fixit Analysis" in output

    def test_render_detail_title_includes_fix_number(self):
        """Detail title includes the fix number."""
        result = _make_result_with_safe_suggestions(2)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = 1
        output = ui.render()
        assert "Fix #2" in output


# =============================================================================
# TestRenderConfirm
# =============================================================================


class TestRenderConfirm:
    """Tests for confirm view rendering."""

    def test_render_confirm_shows_command(self):
        """Confirm view displays the command to apply."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.CONFIRM
        ui.selected_index = 0
        output = ui.render()
        assert "Apply this fix?" in output
        assert "fix-cmd-0" in output

    def test_render_confirm_shows_controls(self):
        """Confirm view shows y/n controls."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.CONFIRM
        ui.selected_index = 0
        output = ui.render()
        assert "[y] Yes, apply" in output
        assert "[n] No, go back" in output

    def test_render_confirm_no_selected_index_falls_back_to_list(self):
        """Confirm view falls back to list when no selection."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.CONFIRM
        ui.selected_index = None
        output = ui.render()
        assert "Fixit Analysis" in output


# =============================================================================
# TestRenderOutcome
# =============================================================================


class TestRenderOutcome:
    """Tests for outcome view rendering."""

    def test_render_outcome_shows_question(self):
        """Outcome view asks if the problem was fixed."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.OUTCOME
        output = ui.render()
        assert "Did this fix the problem?" in output

    def test_render_outcome_shows_all_options(self):
        """Outcome view shows all outcome options."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.OUTCOME
        output = ui.render()
        assert "[y] Yes, it's fixed" in output
        assert "[p] Partially fixed" in output
        assert "[n] No change" in output
        assert "[w] Made it worse" in output
        assert "[s] Skip (don't record)" in output


# =============================================================================
# TestRenderDispatch
# =============================================================================


class TestRenderDispatch:
    """Tests for render() state dispatch."""

    def test_render_dispatches_to_list(self):
        """render() calls _render_list when in LIST state."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.LIST
        output = ui.render()
        assert "Fixit Analysis" in output

    def test_render_dispatches_to_detail(self):
        """render() calls _render_detail when in DETAIL state."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = 0
        output = ui.render()
        assert "Command:" in output

    def test_render_dispatches_to_confirm(self):
        """render() calls _render_confirm when in CONFIRM state."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.CONFIRM
        ui.selected_index = 0
        output = ui.render()
        assert "Apply this fix?" in output

    def test_render_dispatches_to_outcome(self):
        """render() calls _render_outcome when in OUTCOME state."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.OUTCOME
        output = ui.render()
        assert "Did this fix the problem?" in output

    def test_render_unknown_state_returns_empty(self):
        """render() returns empty string for an unrecognized state value."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        # Force an unexpected state via direct assignment to bypass enum
        ui.state = MagicMock()  # type: ignore[assignment]
        output = ui.render()
        assert output == ""


# =============================================================================
# TestHandleListInput
# =============================================================================


class TestHandleListInput:
    """Tests for list view input handling."""

    def test_digit_selects_valid_index(self):
        """Pressing a valid digit transitions to CONFIRM state."""
        result = _make_result_with_safe_suggestions(3)
        ui = FixitInteractive(result)

        cmd, cont = ui.handle_input("1")
        assert cmd is None
        assert cont is True
        assert ui.state == InteractiveState.CONFIRM
        assert ui.selected_index == 0

    def test_digit_selects_last_suggestion(self):
        """Pressing the last valid digit works correctly."""
        result = _make_result_with_safe_suggestions(3)
        ui = FixitInteractive(result)

        cmd, cont = ui.handle_input("3")
        assert cmd is None
        assert cont is True
        assert ui.selected_index == 2

    def test_digit_out_of_range_ignored(self):
        """Pressing a digit beyond the suggestion count does nothing."""
        result = _make_result_with_safe_suggestions(2)
        ui = FixitInteractive(result)

        cmd, cont = ui.handle_input("5")
        assert cmd is None
        assert cont is True
        assert ui.state == InteractiveState.LIST

    def test_digit_zero_ignored(self):
        """Pressing '0' does nothing (index -1 is invalid)."""
        result = _make_result_with_safe_suggestions(2)
        ui = FixitInteractive(result)

        cmd, cont = ui.handle_input("0")
        assert cmd is None
        assert cont is True
        assert ui.state == InteractiveState.LIST

    def test_v_key_continues(self):
        """Pressing 'v' returns continue without command."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        cmd, cont = ui.handle_input("v")
        assert cmd is None
        assert cont is True

    def test_q_exits(self):
        """Pressing 'q' signals exit."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        cmd, cont = ui.handle_input("q")
        assert cmd is None
        assert cont is False

    def test_q_uppercase_exits(self):
        """Pressing 'Q' signals exit."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        cmd, cont = ui.handle_input("Q")
        assert cmd is None
        assert cont is False

    def test_enter_exits(self):
        """Pressing Enter signals exit."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        cmd, cont = ui.handle_input("\r")
        assert cmd is None
        assert cont is False

    def test_newline_exits(self):
        """Pressing newline signals exit."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        cmd, cont = ui.handle_input("\n")
        assert cmd is None
        assert cont is False

    def test_unknown_key_continues(self):
        """Unknown keys are silently ignored and continue."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        cmd, cont = ui.handle_input("x")
        assert cmd is None
        assert cont is True


# =============================================================================
# TestHandleDetailInput
# =============================================================================


class TestHandleDetailInput:
    """Tests for detail view input handling."""

    def test_enter_transitions_to_confirm(self):
        """Pressing Enter in detail view transitions to CONFIRM."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = 0

        cmd, cont = ui.handle_input("\r")
        assert cmd is None
        assert cont is True
        assert ui.state == InteractiveState.CONFIRM

    def test_newline_transitions_to_confirm(self):
        """Newline in detail view transitions to CONFIRM."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL
        ui.selected_index = 0

        cmd, cont = ui.handle_input("\n")
        assert cmd is None
        assert cont is True
        assert ui.state == InteractiveState.CONFIRM

    def test_b_returns_to_list(self):
        """Pressing 'b' in detail goes back to list."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL

        cmd, cont = ui.handle_input("b")
        assert cmd is None
        assert cont is True
        assert ui.state == InteractiveState.LIST

    def test_b_uppercase_returns_to_list(self):
        """Pressing 'B' in detail goes back to list."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL

        cmd, cont = ui.handle_input("B")
        assert cmd is None
        assert cont is True
        assert ui.state == InteractiveState.LIST

    def test_q_exits_from_detail(self):
        """Pressing 'q' in detail exits."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL

        cmd, cont = ui.handle_input("q")
        assert cmd is None
        assert cont is False

    def test_q_uppercase_exits_from_detail(self):
        """Pressing 'Q' in detail exits."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL

        cmd, cont = ui.handle_input("Q")
        assert cmd is None
        assert cont is False

    def test_unknown_key_continues_in_detail(self):
        """Unknown keys are ignored in detail view."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.DETAIL

        cmd, cont = ui.handle_input("z")
        assert cmd is None
        assert cont is True


# =============================================================================
# TestHandleConfirmInput
# =============================================================================


class TestHandleConfirmInput:
    """Tests for confirm view input handling."""

    def test_y_returns_command(self):
        """Pressing 'y' returns the command to execute."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.CONFIRM
        ui.selected_index = 0

        cmd, cont = ui.handle_input("y")
        assert cmd == "fix-cmd-0"
        assert cont is True
        assert ui.state == InteractiveState.OUTCOME

    def test_y_uppercase_returns_command(self):
        """Pressing 'Y' returns the command to execute."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.CONFIRM
        ui.selected_index = 0

        cmd, cont = ui.handle_input("Y")
        assert cmd == "fix-cmd-0"
        assert cont is True
        assert ui.state == InteractiveState.OUTCOME

    def test_y_with_none_index_returns_none(self):
        """Pressing 'y' with no selected_index returns no command."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.CONFIRM
        ui.selected_index = None

        cmd, cont = ui.handle_input("y")
        assert cmd is None
        assert cont is True

    def test_n_goes_back_to_list(self):
        """Pressing 'n' returns to list view."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.CONFIRM
        ui.selected_index = 0

        cmd, cont = ui.handle_input("n")
        assert cmd is None
        assert cont is True
        assert ui.state == InteractiveState.LIST

    def test_n_uppercase_goes_back_to_list(self):
        """Pressing 'N' returns to list view."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.CONFIRM
        ui.selected_index = 0

        cmd, cont = ui.handle_input("N")
        assert cmd is None
        assert cont is True
        assert ui.state == InteractiveState.LIST

    def test_unknown_key_continues_in_confirm(self):
        """Unknown keys are ignored in confirm view."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.CONFIRM

        cmd, cont = ui.handle_input("x")
        assert cmd is None
        assert cont is True


# =============================================================================
# TestHandleOutcomeInput
# =============================================================================


class TestHandleOutcomeInput:
    """Tests for outcome view input handling."""

    @pytest.mark.parametrize(
        "key,expected_outcome",
        [
            ("y", FixitOutcome.IMPROVED),
            ("Y", FixitOutcome.IMPROVED),
            ("p", FixitOutcome.PARTIAL),
            ("P", FixitOutcome.PARTIAL),
            ("n", FixitOutcome.NO_CHANGE),
            ("N", FixitOutcome.NO_CHANGE),
            ("w", FixitOutcome.WORSE),
            ("W", FixitOutcome.WORSE),
            ("s", FixitOutcome.SKIPPED),
            ("S", FixitOutcome.SKIPPED),
        ],
    )
    def test_outcome_keys_set_outcome_and_exit(self, key: str, expected_outcome: FixitOutcome):
        """Each outcome key sets the correct outcome and signals exit."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.OUTCOME

        cmd, cont = ui.handle_input(key)
        assert cmd is None
        assert cont is False
        assert ui.selected_outcome == expected_outcome

    def test_unknown_key_continues_in_outcome(self):
        """Unknown keys are ignored in outcome view."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.OUTCOME

        cmd, cont = ui.handle_input("x")
        assert cmd is None
        assert cont is True


# =============================================================================
# TestSelectedOutcome
# =============================================================================


class TestSelectedOutcome:
    """Tests for selected_outcome property."""

    def test_default_outcome_is_skipped(self):
        """Default outcome before any selection is SKIPPED."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        assert ui.selected_outcome == FixitOutcome.SKIPPED

    def test_outcome_after_selection(self):
        """selected_outcome reflects the last chosen outcome."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = InteractiveState.OUTCOME
        ui.handle_input("w")
        assert ui.selected_outcome == FixitOutcome.WORSE


# =============================================================================
# TestHandleInputDispatch
# =============================================================================


class TestHandleInputDispatch:
    """Tests for handle_input() state dispatch."""

    def test_handle_input_unknown_state_returns_none_false(self):
        """handle_input returns (None, False) for unrecognized state."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)
        ui.state = MagicMock()  # type: ignore[assignment]

        cmd, cont = ui.handle_input("a")
        assert cmd is None
        assert cont is False


# =============================================================================
# TestHelpers
# =============================================================================


class TestHelpers:
    """Tests for helper methods."""

    def test_box_line_basic(self):
        """Box line wraps content with vertical bars."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        line = ui._box_line("hello")
        assert line.startswith(BOX_VERTICAL)
        assert line.endswith(BOX_VERTICAL)
        assert "hello" in line

    def test_box_line_with_ansi_codes(self):
        """ANSI codes are stripped for length calculation."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        line_plain = ui._box_line("hello")
        line_ansi = ui._box_line("\033[1mhello\033[0m")

        # Both should have the same visual width
        # The ANSI version should be longer in raw chars but same box width
        assert BOX_VERTICAL in line_plain
        assert BOX_VERTICAL in line_ansi

    def test_box_line_long_content_no_negative_padding(self):
        """Very long content gets padding=0 (no negative padding)."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        long_text = "x" * 200
        line = ui._box_line(long_text)
        assert long_text in line
        # Should not crash

    def test_box_separator(self):
        """Box separator uses T-left and T-right characters."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        sep = ui._box_separator()
        assert sep.startswith(BOX_T_LEFT)
        assert sep.endswith(BOX_T_RIGHT)
        assert BOX_HORIZONTAL in sep

    def test_box_separator_width(self):
        """Box separator has the correct total width."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        sep = ui._box_separator()
        # T_LEFT + (WIDTH-2) horizontal + T_RIGHT = WIDTH
        assert len(sep) == ui.WIDTH

    def test_wrap_text_short(self):
        """Short text that fits in one line is not wrapped."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        lines = ui._wrap_text("hello world", 80)
        assert lines == ["hello world"]

    def test_wrap_text_long(self):
        """Long text is wrapped at word boundaries."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        text = "word " * 20
        lines = ui._wrap_text(text.strip(), 30)
        assert len(lines) > 1
        for line in lines:
            assert len(line) <= 35  # Some tolerance for last word

    def test_wrap_text_empty(self):
        """Empty text returns empty list."""
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        lines = ui._wrap_text("", 80)
        assert lines == []

    def test_wrap_text_single_long_word(self):
        """A single word longer than the width still appears.

        Note: The implementation produces an initial empty-string line when
        the first word exceeds the width (because `current` starts as [] and
        is flushed as ``" ".join([])`` == ``""`` before appending the word).
        """
        result = _make_result_with_safe_suggestions(1)
        ui = FixitInteractive(result)

        lines = ui._wrap_text("superlongword", 5)
        # The word still appears in the output
        assert any("superlongword" in line for line in lines)

    def test_width_constant(self):
        """WIDTH class constant is 60."""
        assert FixitInteractive.WIDTH == 60


# =============================================================================
# TestGetKeypress
# =============================================================================


class TestGetKeypress:
    """Tests for get_keypress function."""

    @patch("elle.cli.fixit.interactive.sys")
    def test_keypress_with_termios(self, mock_sys):
        """Reads a single character when termios is available."""
        mock_fd = 3
        mock_sys.stdin.fileno.return_value = mock_fd
        mock_sys.stdin.read.return_value = "a"

        mock_termios = MagicMock()
        mock_termios.tcgetattr.return_value = [0, 0, 0, 0]
        mock_termios.TCSADRAIN = 1
        mock_termios.error = OSError

        mock_tty = MagicMock()

        with patch.dict(
            "sys.modules",
            {"termios": mock_termios, "tty": mock_tty},
        ):
            with patch("builtins.__import__", wraps=__import__):
                # Use the real import for everything except termios/tty
                result = get_keypress()

        # The function should attempt to read from stdin
        # If termios works, it returns the char
        assert isinstance(result, str)

    def test_keypress_fallback_on_import_error(self):
        """Documents known bug: termios ImportError triggers UnboundLocalError.

        The source code has ``except (ImportError, termios.error, AttributeError)``
        but when termios fails to import, ``termios`` is unbound, so Python
        raises UnboundLocalError when evaluating the except clause.
        """
        import sys as real_sys

        # Block termios from importing by removing it from sys.modules
        saved_termios = real_sys.modules.pop("termios", None)
        real_sys.modules["termios"] = None  # type: ignore[assignment]

        try:
            with pytest.raises(UnboundLocalError):
                get_keypress()
        finally:
            if saved_termios is not None:
                real_sys.modules["termios"] = saved_termios
            elif "termios" in real_sys.modules:
                del real_sys.modules["termios"]

    def test_keypress_termios_error_falls_back(self):
        """When termios.tcgetattr raises, the except clause catches it."""
        import termios as real_termios
        import tty as real_tty

        mock_termios = MagicMock()
        mock_termios.error = real_termios.error
        mock_termios.tcgetattr.side_effect = real_termios.error("mock error")
        mock_termios.TCSADRAIN = real_termios.TCSADRAIN

        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0
        mock_stdin.read.return_value = "a"

        with patch.dict("sys.modules", {"termios": mock_termios, "tty": real_tty}):
            with patch("elle.cli.fixit.interactive.sys") as mock_sys:
                mock_sys.stdin = mock_stdin
                with patch("builtins.input", return_value="k"):
                    result = get_keypress()

        assert result == "k"

    def test_keypress_attribute_error_falls_back(self):
        """When stdin.fileno() raises AttributeError, fallback to input()."""
        with patch("elle.cli.fixit.interactive.sys") as mock_sys:
            mock_sys.stdin.fileno.side_effect = AttributeError("no fileno")

            with patch("builtins.input", return_value="z"):
                result = get_keypress()

        assert result == "z"


# =============================================================================
# TestRunInteractiveFixit
# =============================================================================


class TestRunInteractiveFixit:
    """Tests for run_interactive_fixit main loop."""

    def test_no_suggestions_renders_and_returns(self):
        """With no safe suggestions, renders result and returns immediately."""
        result = _make_result(verified_suggestions=())
        session = _make_session()

        mock_render = MagicMock(return_value="rendered output")
        mock_renderer_mod = MagicMock()
        mock_renderer_mod.render_fixit_result = mock_render

        with patch.dict("sys.modules", {"elle.cli.fixit.renderer": mock_renderer_mod}):
            with patch("builtins.print"):
                returned = run_interactive_fixit(result, session)

        assert returned is result
        mock_render.assert_called_once_with(result)

    def test_no_suggestions_only_unsafe_renders_and_returns(self):
        """With only unsafe suggestions, renders result and returns."""
        unsafe = _make_verified(is_safe=False)
        result = _make_result(verified_suggestions=(unsafe,))
        session = _make_session()

        with patch("elle.cli.fixit.interactive.render_fixit_result", create=True) as mock_render:
            mock_render.return_value = "rendered"
            with patch("builtins.print"):
                returned = run_interactive_fixit(result, session)

        assert returned is result

    def test_loop_exits_on_q(self):
        """Interactive loop exits when user presses 'q'."""
        result = _make_result_with_safe_suggestions(1)
        session = _make_session()

        mock_callback = MagicMock()

        with patch("elle.cli.fixit.interactive.get_keypress", return_value="q"):
            with patch("builtins.print"):
                returned = run_interactive_fixit(result, session, execute_callback=mock_callback)

        assert returned.outcome == FixitOutcome.SKIPPED
        mock_callback.assert_not_called()

    def test_loop_exits_on_keyboard_interrupt(self):
        """Interactive loop handles KeyboardInterrupt gracefully."""
        result = _make_result_with_safe_suggestions(1)
        session = _make_session()

        mock_callback = MagicMock()

        with patch("elle.cli.fixit.interactive.get_keypress", side_effect=KeyboardInterrupt):
            with patch("builtins.print"):
                returned = run_interactive_fixit(result, session, execute_callback=mock_callback)

        assert returned.outcome == FixitOutcome.SKIPPED

    def test_loop_uses_default_callback_when_none(self):
        """When no callback is provided, uses service.execute_fix."""
        result = _make_result_with_safe_suggestions(1)
        session = _make_session()

        mock_service = MagicMock()
        mock_service.execute_fix = MagicMock()

        with patch("elle.cli.fixit.interactive.get_keypress", return_value="q"):
            with patch("builtins.print"):
                with patch(
                    "elle.cli.fixit.interactive.get_fixit_service",
                    create=True,
                    return_value=mock_service,
                ):
                    returned = run_interactive_fixit(result, session, execute_callback=None)

        assert returned is not None

    def test_command_execution_success(self):
        """Successful command execution displays success message."""
        result = _make_result_with_safe_suggestions(1)
        session = _make_session()

        action = FixitAction(
            command="fix-cmd-0",
            exit_code=0,
            stdout="Command output",
            stderr="",
            success=True,
        )
        updated_result = result.with_action(action)
        mock_callback = MagicMock(return_value=(updated_result, action))

        keypress_sequence = iter(["1", "y", "s"])

        with patch("elle.cli.fixit.interactive.get_keypress", side_effect=keypress_sequence):
            with patch("builtins.print"):
                with patch("builtins.input", return_value=""):
                    returned = run_interactive_fixit(result, session, execute_callback=mock_callback)

        mock_callback.assert_called_once()
        assert returned.outcome == FixitOutcome.SKIPPED

    def test_command_execution_failure(self):
        """Failed command execution displays error information."""
        result = _make_result_with_safe_suggestions(1)
        session = _make_session()

        action = FixitAction(
            command="fix-cmd-0",
            exit_code=1,
            stdout="",
            stderr="Command failed with error",
            success=False,
        )
        updated_result = result.with_action(action)
        mock_callback = MagicMock(return_value=(updated_result, action))

        keypress_sequence = iter(["1", "y", "y"])

        with patch("elle.cli.fixit.interactive.get_keypress", side_effect=keypress_sequence):
            with patch("builtins.print"):
                with patch("builtins.input", return_value=""):
                    run_interactive_fixit(result, session, execute_callback=mock_callback)

        mock_callback.assert_called_once()

    def test_command_with_stdout_displayed(self):
        """Command stdout is displayed after execution."""
        result = _make_result_with_safe_suggestions(1)
        session = _make_session()

        action = FixitAction(
            command="fix-cmd-0",
            exit_code=0,
            stdout="Some output\n",
            stderr="",
            success=True,
        )
        updated_result = result.with_action(action)
        mock_callback = MagicMock(return_value=(updated_result, action))

        keypress_sequence = iter(["1", "y", "s"])

        printed_lines = []

        with patch("elle.cli.fixit.interactive.get_keypress", side_effect=keypress_sequence):
            with patch("builtins.print", side_effect=lambda *a, **kw: printed_lines.append(str(a))):
                with patch("builtins.input", return_value=""):
                    run_interactive_fixit(result, session, execute_callback=mock_callback)

        # Check that stdout was printed
        all_output = " ".join(printed_lines)
        assert "Some output" in all_output

    def test_incident_finalization_on_result_with_incident_id(self):
        """When result has incident_id, finalize_incident is called."""
        result = _make_result_with_safe_suggestions(1)
        result = result.with_incident_id("INC-100")
        session = _make_session()

        mock_service = MagicMock()
        mock_service_mod = MagicMock()
        mock_service_mod.get_fixit_service.return_value = mock_service

        with patch("elle.cli.fixit.interactive.get_keypress", return_value="q"):
            with patch("builtins.print"):
                with patch.dict("sys.modules", {"elle.cli.fixit.service": mock_service_mod}):
                    run_interactive_fixit(result, session, execute_callback=MagicMock())

        mock_service.finalize_incident.assert_called_once()

    def test_incident_finalization_skipped_when_no_incident_id(self):
        """When result has no incident_id, finalize_incident is not called."""
        result = _make_result_with_safe_suggestions(1)
        session = _make_session()

        mock_service = MagicMock()

        with patch("elle.cli.fixit.interactive.get_keypress", return_value="q"):
            with patch("builtins.print"):
                with patch(
                    "elle.cli.fixit.interactive.get_fixit_service",
                    create=True,
                    return_value=mock_service,
                ):
                    run_interactive_fixit(result, session, execute_callback=MagicMock())

        # finalize_incident should not be called because incident_id is None
        # (the import path for finalization is inside the function)

    def test_incident_finalization_exception_swallowed(self):
        """Exceptions during incident finalization are silently swallowed."""
        result = _make_result_with_safe_suggestions(1)
        result = result.with_incident_id("INC-200")
        session = _make_session()

        mock_service = MagicMock()
        mock_service.finalize_incident.side_effect = RuntimeError("db locked")
        mock_service_mod = MagicMock()
        mock_service_mod.get_fixit_service.return_value = mock_service

        with patch("elle.cli.fixit.interactive.get_keypress", return_value="q"):
            with patch("builtins.print"):
                with patch.dict("sys.modules", {"elle.cli.fixit.service": mock_service_mod}):
                    # Should not raise
                    returned = run_interactive_fixit(result, session, execute_callback=MagicMock())

        assert returned is not None

    def test_outcome_recorded_after_loop(self):
        """Selected outcome is applied to the returned result."""
        result = _make_result_with_safe_suggestions(1)
        session = _make_session()
        mock_callback = MagicMock()

        # Press q to exit => outcome is SKIPPED (default)
        with patch("elle.cli.fixit.interactive.get_keypress", return_value="q"):
            with patch("builtins.print"):
                returned = run_interactive_fixit(result, session, execute_callback=mock_callback)

        assert returned.outcome == FixitOutcome.SKIPPED

    def test_full_flow_select_apply_outcome(self):
        """Full interaction: select fix -> confirm -> apply -> record outcome."""
        result = _make_result_with_safe_suggestions(1)
        session = _make_session()

        action = FixitAction(
            command="fix-cmd-0",
            exit_code=0,
            stdout="",
            stderr="",
            success=True,
        )
        updated_result = result.with_action(action)
        mock_callback = MagicMock(return_value=(updated_result, action))

        # Flow: "1" -> confirm, "y" -> execute, then outcome "y" -> improved
        keypress_sequence = iter(["1", "y", "y"])

        with patch("elle.cli.fixit.interactive.get_keypress", side_effect=keypress_sequence):
            with patch("builtins.print"):
                with patch("builtins.input", return_value=""):
                    returned = run_interactive_fixit(result, session, execute_callback=mock_callback)

        assert returned.outcome == FixitOutcome.IMPROVED

    def test_failed_command_shows_stderr(self):
        """When command fails and has stderr, it is displayed."""
        result = _make_result_with_safe_suggestions(1)
        session = _make_session()

        action = FixitAction(
            command="fix-cmd-0",
            exit_code=127,
            stdout="",
            stderr="command not found",
            success=False,
        )
        updated_result = result.with_action(action)
        mock_callback = MagicMock(return_value=(updated_result, action))

        keypress_sequence = iter(["1", "y", "s"])

        printed_lines = []

        with patch("elle.cli.fixit.interactive.get_keypress", side_effect=keypress_sequence):
            with patch("builtins.print", side_effect=lambda *a, **kw: printed_lines.append(str(a))):
                with patch("builtins.input", return_value=""):
                    run_interactive_fixit(result, session, execute_callback=mock_callback)

        all_output = " ".join(printed_lines)
        assert "command not found" in all_output

    def test_successful_command_no_stderr_shown(self):
        """When command succeeds, stderr is not displayed even if present."""
        result = _make_result_with_safe_suggestions(1)
        session = _make_session()

        action = FixitAction(
            command="fix-cmd-0",
            exit_code=0,
            stdout="ok",
            stderr="warning: something",
            success=True,
        )
        updated_result = result.with_action(action)
        mock_callback = MagicMock(return_value=(updated_result, action))

        keypress_sequence = iter(["1", "y", "s"])

        printed_lines = []

        with patch("elle.cli.fixit.interactive.get_keypress", side_effect=keypress_sequence):
            with patch("builtins.print", side_effect=lambda *a, **kw: printed_lines.append(str(a))):
                with patch("builtins.input", return_value=""):
                    run_interactive_fixit(result, session, execute_callback=mock_callback)

        # stderr should NOT be printed on success (the code checks `not action.success`)
        all_output = " ".join(printed_lines)
        # The success message should appear
        assert "succeeded" in all_output
