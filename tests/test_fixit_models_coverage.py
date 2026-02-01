"""Additional coverage tests for elle.cli.fixit.models.

Targets uncovered lines:
- L123: has_vault_context() with man_snippets
- L131: primary_source returning 'incident_vault'
- L135: primary_source returning 'man_vault'
- L141-155: build_rationale_summary() with various inputs
- L163-218: to_rationale_kwargs() full computation
- L477: with_analysis method (already covered but line may be missed)
- L520: with_outcome method
- L547: has_suggestions property
- L552: safe_suggestions property
"""

from __future__ import annotations

from pathlib import Path

from elle.cli.fixit.models import (
    CommandFailure,
    FixCommand,
    FixitAction,
    FixitAnalysis,
    FixitContext,
    FixitDiagnosis,
    FixitOutcome,
    FixitResult,
    ManSnippetContext,
    PriorArtContext,
    SuccessfulAction,
    VerificationResult,
    VerificationStatus,
    VerifiedFixCommand,
)


# =============================================================================
# Helpers
# =============================================================================


def _failure() -> CommandFailure:
    return CommandFailure(command="test cmd", exit_code=1, cwd=Path("/tmp"))


def _diag() -> FixitDiagnosis:
    return FixitDiagnosis(
        error_category="other",
        summary="Test",
        root_cause="Test",
        confidence=0.5,
    )


def _context(**kw) -> FixitContext:
    return FixitContext(failure=kw.get("failure", _failure()), **{k: v for k, v in kw.items() if k != "failure"})


# =============================================================================
# FixitContext.has_vault_context (line 123)
# =============================================================================


class TestFixitContextHasVaultContext:
    """Cover has_vault_context method."""

    def test_with_man_snippets_only(self):
        """has_vault_context returns True when man_snippets present."""
        ctx = _context(
            man_snippets=(
                ManSnippetContext(name="ls", section="1", snippet="list dir"),
            ),
        )
        assert ctx.has_vault_context() is True

    def test_with_prior_art_only(self):
        """has_vault_context returns True when prior_art present."""
        ctx = _context(
            prior_art=(
                PriorArtContext(incident_id="INC-1", title="test", outcome="improved"),
            ),
        )
        assert ctx.has_vault_context() is True

    def test_with_both(self):
        """has_vault_context returns True when both present."""
        ctx = _context(
            man_snippets=(
                ManSnippetContext(name="ls", section="1", snippet="list"),
            ),
            prior_art=(
                PriorArtContext(incident_id="INC-1", title="test", outcome="improved"),
            ),
        )
        assert ctx.has_vault_context() is True

    def test_with_neither(self):
        """has_vault_context returns False when both empty."""
        ctx = _context()
        assert ctx.has_vault_context() is False


# =============================================================================
# FixitContext.primary_source (lines 131, 135)
# =============================================================================


class TestFixitContextPrimarySource:
    """Cover primary_source property."""

    def test_incident_vault_with_good_prior_art(self):
        """Returns 'incident_vault' when good prior art exists."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-1",
                    title="test",
                    outcome="improved",
                    score=0.8,
                ),
            ),
        )
        assert ctx.primary_source == "incident_vault"

    def test_incident_vault_with_partial_outcome(self):
        """Returns 'incident_vault' for partial outcome with high score."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-2",
                    title="test",
                    outcome="partial",
                    score=0.7,
                ),
            ),
        )
        assert ctx.primary_source == "incident_vault"

    def test_man_vault_when_no_good_prior_art(self):
        """Returns 'man_vault' when only man snippets present (no good prior art)."""
        ctx = _context(
            man_snippets=(
                ManSnippetContext(name="ls", section="1", snippet="list dir"),
            ),
            prior_art=(
                PriorArtContext(
                    incident_id="INC-3",
                    title="bad outcome",
                    outcome="worse",
                    score=0.9,
                ),
            ),
        )
        assert ctx.primary_source == "man_vault"

    def test_man_vault_no_prior_art(self):
        """Returns 'man_vault' when only man snippets and no prior art."""
        ctx = _context(
            man_snippets=(
                ManSnippetContext(name="grep", section="1", snippet="search"),
            ),
        )
        assert ctx.primary_source == "man_vault"

    def test_llm_only_when_nothing(self):
        """Returns 'llm_only' when no context."""
        ctx = _context()
        assert ctx.primary_source == "llm_only"

    def test_low_score_prior_art_returns_man_vault(self):
        """Returns 'man_vault' when prior art has good outcome but low score."""
        ctx = _context(
            man_snippets=(
                ManSnippetContext(name="ls", section="1", snippet="list"),
            ),
            prior_art=(
                PriorArtContext(
                    incident_id="INC-4",
                    title="test",
                    outcome="improved",
                    score=0.3,  # Below 0.5 threshold
                ),
            ),
        )
        assert ctx.primary_source == "man_vault"


# =============================================================================
# FixitContext.build_rationale_summary (lines 141-155)
# =============================================================================


class TestBuildRationaleSummary:
    """Cover build_rationale_summary method."""

    def test_with_man_snippets(self):
        """Summary references man pages."""
        ctx = _context(
            man_snippets=(
                ManSnippetContext(name="ls", section="1", snippet="list"),
                ManSnippetContext(name="grep", section="1", snippet="search"),
                ManSnippetContext(name="find", section="1", snippet="walk tree"),
            ),
        )
        summary = ctx.build_rationale_summary()
        assert "man" in summary
        assert "ls(1)" in summary
        assert "grep(1)" in summary
        # Only first 2 are shown
        assert "find(1)" not in summary

    def test_with_successful_prior_art(self):
        """Summary references successful incidents."""
        ctx = _context(
            prior_art=(
                PriorArtContext(incident_id="INC-1", title="t", outcome="improved"),
                PriorArtContext(incident_id="INC-2", title="t", outcome="partial"),
                PriorArtContext(incident_id="INC-3", title="t", outcome="worse"),
            ),
        )
        summary = ctx.build_rationale_summary()
        assert "2 similar incident(s) succeeded" in summary

    def test_with_both_sources(self):
        """Summary combines both man and incident references."""
        ctx = _context(
            man_snippets=(
                ManSnippetContext(name="ls", section="1", snippet="list"),
            ),
            prior_art=(
                PriorArtContext(incident_id="INC-1", title="t", outcome="improved"),
            ),
        )
        summary = ctx.build_rationale_summary()
        assert "man" in summary
        assert "similar incident(s) succeeded" in summary
        assert "Suggested because" in summary

    def test_no_context_returns_llm_message(self):
        """No context returns LLM-only message."""
        ctx = _context()
        summary = ctx.build_rationale_summary()
        assert summary == "Suggested by LLM analysis"

    def test_prior_art_no_successes(self):
        """Prior art with only bad outcomes doesn't add incident line."""
        ctx = _context(
            prior_art=(
                PriorArtContext(incident_id="INC-1", title="t", outcome="worse"),
                PriorArtContext(incident_id="INC-2", title="t", outcome="no_change"),
            ),
        )
        summary = ctx.build_rationale_summary()
        # No successes, so should be LLM-only
        assert summary == "Suggested by LLM analysis"


# =============================================================================
# FixitContext.to_rationale_kwargs (lines 163-218)
# =============================================================================


class TestToRationaleKwargs:
    """Cover to_rationale_kwargs method."""

    def test_empty_context(self):
        """No vault context produces None citations and show_rationale=False."""
        ctx = _context()
        kw = ctx.to_rationale_kwargs()
        assert kw["man_citations"] is None
        assert kw["incident_citations"] is None
        assert kw["show_rationale"] is False
        assert kw["rationale_summary"] == "Suggested by LLM analysis"
        # Confidence should be base 0.5 from LLM
        assert kw["confidence_breakdown"]["overall"] == 0.5
        assert kw["confidence_breakdown"]["from_man_vault"] == 0.0
        assert kw["confidence_breakdown"]["from_incident_vault"] == 0.0

    def test_with_man_citations(self):
        """Man snippets produce man_citations and boost confidence."""
        ctx = _context(
            man_snippets=(
                ManSnippetContext(
                    name="ls",
                    section="1",
                    snippet="list directory contents",
                    match_section="DESCRIPTION",
                    relevance_score=0.9,
                ),
            ),
        )
        kw = ctx.to_rationale_kwargs()
        assert kw["man_citations"] is not None
        assert len(kw["man_citations"]) == 1
        assert kw["man_citations"][0]["name"] == "ls"
        assert kw["man_citations"][0]["match_section"] == "DESCRIPTION"
        assert kw["confidence_breakdown"]["from_man_vault"] > 0.0
        assert kw["confidence_breakdown"]["overall"] > 0.5
        assert kw["show_rationale"] is True

    def test_with_incident_citations_good_outcome(self):
        """Incident citations with good outcome boost confidence."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-1",
                    title="Package fix",
                    outcome="improved",
                    score=0.85,
                    match_type="semantic",
                    successful_actions=(
                        SuccessfulAction(command="apt update"),
                        SuccessfulAction(command="apt install foo"),
                    ),
                ),
            ),
        )
        kw = ctx.to_rationale_kwargs()
        assert kw["incident_citations"] is not None
        assert len(kw["incident_citations"]) == 1
        assert kw["incident_citations"][0]["incident_id"] == "INC-1"
        assert kw["incident_citations"][0]["outcome"] == "improved"
        assert kw["incident_citations"][0]["match_type"] == "semantic"
        assert kw["incident_citations"][0]["successful_actions"] == [
            "apt update",
            "apt install foo",
        ]
        assert kw["confidence_breakdown"]["from_incident_vault"] > 0.0
        assert kw["confidence_breakdown"]["overall"] > 0.5

    def test_with_bad_outcome_incidents_no_incident_boost(self):
        """Incident citations with bad outcome don't boost confidence."""
        ctx = _context(
            prior_art=(
                PriorArtContext(
                    incident_id="INC-2",
                    title="Failed fix",
                    outcome="worse",
                    score=0.9,
                ),
            ),
        )
        kw = ctx.to_rationale_kwargs()
        assert kw["incident_citations"] is not None
        assert kw["confidence_breakdown"]["from_incident_vault"] == 0.0

    def test_with_both_man_and_incidents(self):
        """Both sources produce combined confidence."""
        ctx = _context(
            man_snippets=(
                ManSnippetContext(
                    name="ls",
                    section="1",
                    snippet="list",
                    relevance_score=0.8,
                ),
            ),
            prior_art=(
                PriorArtContext(
                    incident_id="INC-1",
                    title="fix",
                    outcome="improved",
                    score=0.7,
                    successful_actions=(SuccessfulAction(command="apt update"),),
                ),
            ),
        )
        kw = ctx.to_rationale_kwargs()
        conf = kw["confidence_breakdown"]
        assert conf["from_man_vault"] > 0.0
        assert conf["from_incident_vault"] > 0.0
        assert conf["overall"] <= 1.0
        assert conf["overall"] >= 0.5
        assert kw["show_rationale"] is True

    def test_match_section_none_becomes_empty_string(self):
        """Man citation with match_section=None converts to empty string."""
        ctx = _context(
            man_snippets=(
                ManSnippetContext(
                    name="cat",
                    section="1",
                    snippet="concatenate",
                    match_section=None,
                    relevance_score=0.5,
                ),
            ),
        )
        kw = ctx.to_rationale_kwargs()
        assert kw["man_citations"][0]["match_section"] == ""

    def test_overall_capped_at_one(self):
        """Overall confidence is capped at 1.0."""
        ctx = _context(
            man_snippets=(
                ManSnippetContext(name="a", section="1", snippet="x", relevance_score=1.0),
            ),
            prior_art=(
                PriorArtContext(
                    incident_id="INC",
                    title="t",
                    outcome="improved",
                    score=1.0,
                ),
            ),
        )
        kw = ctx.to_rationale_kwargs()
        assert kw["confidence_breakdown"]["overall"] <= 1.0


# =============================================================================
# FixitResult.with_incident_id (line 531-542)
# =============================================================================


class TestFixitResultWithIncidentId:
    """Cover with_incident_id method."""

    def test_with_incident_id(self):
        """with_incident_id returns a new result with the incident_id set."""
        ctx = _context()
        result = FixitResult(context=ctx)
        assert result.incident_id is None

        new = result.with_incident_id("INC-999")
        assert new.incident_id == "INC-999"
        assert result.incident_id is None  # original unchanged

    def test_with_incident_id_preserves_other_fields(self):
        """with_incident_id preserves analysis, outcome, actions etc."""
        ctx = _context()
        action = FixitAction(command="ls", exit_code=0, success=True)
        analysis = FixitAnalysis(diagnosis=_diag())

        result = FixitResult(
            context=ctx,
            analysis=analysis,
            actions=(action,),
            outcome=FixitOutcome.IMPROVED,
            used_fallback=True,
        )

        new = result.with_incident_id("INC-123")
        assert new.incident_id == "INC-123"
        assert new.analysis == analysis
        assert new.actions == (action,)
        assert new.outcome == FixitOutcome.IMPROVED
        assert new.used_fallback is True


# =============================================================================
# FixitResult.with_verified_suggestions (line 488-503)
# =============================================================================


class TestFixitResultWithVerifiedSuggestions:
    """Cover with_verified_suggestions method."""

    def test_with_verified_suggestions_and_errors(self):
        """Method returns new result with suggestions and errors."""
        ctx = _context()
        result = FixitResult(context=ctx)

        fix = FixCommand(command="ls", explanation="list", risk_level="safe")
        ver = VerificationResult(command="ls", status=VerificationStatus.PASSED)
        verified = VerifiedFixCommand(fix=fix, verification=ver, is_safe=True)

        new = result.with_verified_suggestions((verified,), ("some error",))
        assert len(new.verified_suggestions) == 1
        assert len(new.verification_errors) == 1
        assert new.verification_errors[0] == "some error"
        # Original unchanged
        assert len(result.verified_suggestions) == 0


# =============================================================================
# FixitAction model construction
# =============================================================================


class TestFixitActionConstruction:
    """Cover FixitAction model edge cases."""

    def test_with_all_fields(self):
        """FixitAction with all fields populated."""
        action = FixitAction(
            command="apt update",
            exit_code=0,
            stdout="Hit:1 http://archive.ubuntu.com",
            stderr="",
            success=True,
            privileged=True,
            duration_ms=500,
        )
        assert action.command == "apt update"
        assert action.privileged is True
        assert action.duration_ms == 500
        assert action.executed_at is not None

    def test_default_fields(self):
        """FixitAction with only required fields uses defaults."""
        action = FixitAction(command="ls", exit_code=0, success=True)
        assert action.stdout == ""
        assert action.stderr == ""
        assert action.privileged is False
        assert action.duration_ms is None


# =============================================================================
# SuccessfulAction model construction
# =============================================================================


class TestSuccessfulActionConstruction:
    """Cover SuccessfulAction model."""

    def test_defaults(self):
        """SuccessfulAction has sensible defaults."""
        action = SuccessfulAction(command="apt update")
        assert action.kind == "shell"
        assert action.exit_code == 0

    def test_custom_fields(self):
        """SuccessfulAction with custom kind and exit_code."""
        action = SuccessfulAction(command="service.restart", kind="capability", exit_code=0)
        assert action.kind == "capability"
