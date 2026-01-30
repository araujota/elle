"""Pydantic models for the Fixit command.

Defines data structures for command failure analysis, LLM responses,
verification results, and fixit outcomes.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Input Models
# =============================================================================


class CommandFailure(BaseModel):
    """Represents a failed command to analyze.

    Captures all information needed to diagnose a command failure:
    the command itself, exit code, outputs, and context.
    """

    model_config = ConfigDict(frozen=True)

    command: str = Field(description="The failed command")
    exit_code: int = Field(description="Non-zero exit code")
    stdout: str = Field(default="", description="Standard output")
    stderr: str = Field(default="", description="Error output")
    cwd: Path = Field(default_factory=Path.cwd, description="Working directory")


class ManSnippetContext(BaseModel):
    """Summarized Man Vault search result for context."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Command name")
    section: str = Field(description="Man section")
    snippet: str = Field(description="Relevant excerpt")
    match_section: str | None = Field(default=None, description="Section heading")
    relevance_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Relevance score from search",
    )


class SuccessfulAction(BaseModel):
    """A successful action from a prior incident."""

    model_config = ConfigDict(frozen=True)

    command: str
    kind: str = "shell"
    exit_code: int = 0


class PriorArtContext(BaseModel):
    """Summarized prior incident for context.

    Includes successful actions so the LLM can reference
    what worked in similar past situations.
    """

    model_config = ConfigDict(frozen=True)

    incident_id: str
    title: str
    outcome: str
    summary: str = ""
    decision: dict[str, Any] = Field(default_factory=dict)
    root_cause: str | None = None
    verification_steps: tuple[str, ...] = ()
    successful_actions: tuple[SuccessfulAction, ...] = Field(
        default_factory=tuple,
        description="Commands that successfully fixed the issue",
    )
    trigger_command: str | None = Field(
        default=None,
        description="Original command that caused the incident",
    )
    score: float = Field(default=0.0, description="Relevance score")
    outcome_weight: float = Field(default=0.5, description="Outcome quality weight")
    precondition_match: float = Field(default=0.0, description="How well preconditions match")
    days_ago: int = Field(default=0, description="Days since this incident")
    match_type: Literal["fingerprint", "lexical", "semantic", "hybrid", "daemon_api"] = Field(
        default="hybrid",
        description="How this prior art was matched",
    )


class FixitContext(BaseModel):
    """Complete context for fixit analysis.

    Combines command failure information with documentation
    and prior art from the vaults.
    """

    model_config = ConfigDict(frozen=True)

    failure: CommandFailure
    history: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Recent command history",
    )
    man_snippets: tuple[ManSnippetContext, ...] = Field(
        default_factory=tuple,
        description="Relevant Man Vault documentation",
    )
    prior_art: tuple[PriorArtContext, ...] = Field(
        default_factory=tuple,
        description="Similar past incidents",
    )

    def has_vault_context(self) -> bool:
        """Check if we have context from vaults."""
        return bool(self.man_snippets) or bool(self.prior_art)

    @property
    def primary_source(self) -> Literal["man_vault", "incident_vault", "llm_only"]:
        """Determine the primary source of context."""
        # Prior incidents with good outcomes are most valuable
        good_prior = [p for p in self.prior_art if p.outcome in ("improved", "partial") and p.score > 0.5]
        if good_prior:
            return "incident_vault"

        # Man pages provide documentation grounding
        if self.man_snippets:
            return "man_vault"

        return "llm_only"

    def build_rationale_summary(self) -> str:
        """Build a summary string for rationale display."""
        parts = []

        if self.man_snippets:
            man_refs = [f"{m.name}({m.section})" for m in self.man_snippets[:2]]
            parts.append(f"man {', '.join(man_refs)}")

        if self.prior_art:
            successful = sum(1 for p in self.prior_art if p.outcome in ("improved", "partial"))
            if successful:
                parts.append(f"{successful} similar incident(s) succeeded")

        if not parts:
            return "Suggested by LLM analysis"

        return "Suggested because " + " + ".join(parts)

    def to_rationale_kwargs(self) -> dict[str, Any]:
        """Convert context to kwargs for rationale panel display.

        Returns:
            Dict of kwargs for fixit_panel's rationale display.
        """
        man_citations = [
            {
                "name": m.name,
                "section": m.section,
                "snippet": m.snippet,
                "relevance_score": m.relevance_score,
                "match_section": m.match_section or "",
            }
            for m in self.man_snippets
        ]

        incident_citations = [
            {
                "incident_id": p.incident_id,
                "title": p.title,
                "outcome": p.outcome,
                "similarity_score": p.score,
                "match_type": p.match_type,
                "successful_actions": [a.command for a in p.successful_actions],
            }
            for p in self.prior_art
        ]

        # Build confidence breakdown based on sources
        overall = 0.5  # Default LLM confidence
        from_man = 0.0
        from_inc = 0.0

        if man_citations:
            scores: list[float] = []
            for c in man_citations:
                val = c.get("relevance_score")
                scores.append(float(val) if val is not None else 0.0)  # type: ignore[arg-type]
            from_man = max(scores) * 0.3 if scores else 0.0
            overall += from_man

        if incident_citations:
            good_outcomes = [c for c in incident_citations if c.get("outcome") in ("improved", "partial")]
            if good_outcomes:
                sim_scores: list[float] = []
                for c in good_outcomes:
                    val = c.get("similarity_score")
                    sim_scores.append(float(val) if val is not None else 0.0)  # type: ignore[arg-type]
                from_inc = max(sim_scores) * 0.4 if sim_scores else 0.0
                overall += from_inc

        overall = min(1.0, overall)

        confidence = {
            "overall": overall,
            "from_man_vault": from_man,
            "from_incident_vault": from_inc,
            "from_llm": max(0.0, 0.5 - from_man - from_inc),
        }

        return {
            "man_citations": man_citations if man_citations else None,
            "incident_citations": incident_citations if incident_citations else None,
            "confidence_breakdown": confidence,
            "rationale_summary": self.build_rationale_summary(),
            "show_rationale": self.has_vault_context(),
        }


# =============================================================================
# LLM Analysis Models
# =============================================================================


# Error categories that the LLM can diagnose
ErrorCategory = Literal[
    # Original categories
    "command_not_found",
    "permission_denied",
    "file_not_found",
    "syntax_error",
    "argument_error",
    "dependency_missing",
    "resource_exhausted",
    "network_error",
    "configuration_error",
    # Package manager categories
    "apt_lock",
    "dpkg_interrupted",
    "broken_packages",
    "held_packages",
    "apt_hash_mismatch",
    "apt_key_missing",
    # Docker categories
    "docker_not_running",
    "container_not_found",
    "image_not_found",
    "port_in_use",
    "container_runtime_error",
    # Filesystem categories
    "readonly_fs",
    "device_busy",
    "stale_nfs",
    "fs_corrupt",
    "io_error",
    # Service/systemd categories
    "service_start_failed",
    "address_in_use",
    "service_dep_failed",
    "unit_not_found",
    # Database categories
    "db_locked",
    "db_auth_failed",
    "db_connection_refused",
    # Catch-all
    "other",
]

# Risk levels for suggested fixes
RiskLevel = Literal["safe", "moderate", "high"]


class FixitDiagnosis(BaseModel):
    """Diagnosis of what went wrong.

    Generated by the LLM to explain the failure.
    """

    model_config = ConfigDict(frozen=True)

    error_category: ErrorCategory = Field(description="Category of the error")
    summary: str = Field(description="One-sentence summary of the problem")
    root_cause: str = Field(description="Explanation of why it failed")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in this diagnosis (0.0-1.0)",
    )


class FixCommand(BaseModel):
    """A suggested fix command.

    Includes the command to run, explanation, and metadata.
    """

    model_config = ConfigDict(frozen=True)

    command: str = Field(description="The fix command to run")
    explanation: str = Field(description="What this command does")
    risk_level: RiskLevel = Field(description="Risk assessment")
    requires_privilege: bool = Field(
        default=False,
        description="Whether elevated privileges are needed",
    )
    verification: str | None = Field(
        default=None,
        description="Command to verify the fix worked",
    )


class FixitAnalysis(BaseModel):
    """Complete analysis from the LLM.

    Contains diagnosis, suggested fixes, and additional context.
    """

    model_config = ConfigDict(frozen=True)

    diagnosis: FixitDiagnosis
    suggestions: tuple[FixCommand, ...] = Field(
        default_factory=tuple,
        description="Ordered list of fix suggestions (best first)",
    )
    alternative_approach: str | None = Field(
        default=None,
        description="Alternative approach if fixes don't work",
    )
    learn_more: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Suggested man pages to read",
    )


# =============================================================================
# Verification Models
# =============================================================================


class VerificationStatus(str, Enum):
    """Status of command verification."""

    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"


class VerificationResult(BaseModel):
    """Result of verifying a single fix command."""

    model_config = ConfigDict(frozen=True)

    command: str = Field(description="The command that was verified")
    status: VerificationStatus = Field(description="Verification status")
    checks_passed: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Checks that passed",
    )
    checks_failed: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Checks that failed",
    )
    warnings: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Warning messages",
    )


class VerifiedFixCommand(BaseModel):
    """A fix command with verification results."""

    model_config = ConfigDict(frozen=True)

    fix: FixCommand
    verification: VerificationResult
    is_safe: bool = Field(
        description="Whether the command passed all critical checks",
    )


# =============================================================================
# Action and Outcome Models
# =============================================================================


class FixitOutcome(str, Enum):
    """Outcome of applying a fix."""

    IMPROVED = "improved"
    PARTIAL = "partial"
    NO_CHANGE = "no_change"
    WORSE = "worse"
    SKIPPED = "skipped"
    ERROR = "error"


class FixitAction(BaseModel):
    """Record of a fix action taken."""

    model_config = ConfigDict(frozen=True)

    command: str = Field(description="The command that was executed")
    exit_code: int = Field(description="Exit code of the command")
    stdout: str = Field(default="", description="Standard output")
    stderr: str = Field(default="", description="Error output")
    success: bool = Field(description="Whether the command succeeded")
    privileged: bool = Field(default=False, description="Used elevated privileges")
    duration_ms: int | None = Field(
        default=None,
        description="Execution duration in milliseconds",
    )
    executed_at: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# Final Result Model
# =============================================================================


class FixitResult(BaseModel):
    """Complete result of a fixit operation.

    Captures the entire flow: context, analysis, verification,
    actions taken, and final outcome.
    """

    model_config = ConfigDict(frozen=True)

    # Input
    context: FixitContext

    # Analysis (may be None if LLM unavailable)
    analysis: FixitAnalysis | None = None

    # Verified suggestions
    verified_suggestions: tuple[VerifiedFixCommand, ...] = Field(
        default_factory=tuple,
        description="Suggestions with verification results",
    )

    # Verification errors (commands filtered out)
    verification_errors: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Errors from verification (commands removed)",
    )

    # Actions taken
    actions: tuple[FixitAction, ...] = Field(
        default_factory=tuple,
        description="Fix commands that were executed",
    )

    # Outcome
    outcome: FixitOutcome = Field(default=FixitOutcome.SKIPPED)

    # Incident tracking
    incident_id: str | None = Field(
        default=None,
        description="Linked Incident Vault ID",
    )

    # Fallback mode indicator
    used_fallback: bool = Field(
        default=False,
        description="Whether rule-based fallback was used",
    )

    def with_analysis(self, analysis: FixitAnalysis) -> FixitResult:
        """Return a new result with the given analysis."""
        return FixitResult(
            context=self.context,
            analysis=analysis,
            verified_suggestions=self.verified_suggestions,
            verification_errors=self.verification_errors,
            actions=self.actions,
            outcome=self.outcome,
            incident_id=self.incident_id,
            used_fallback=self.used_fallback,
        )

    def with_verified_suggestions(
        self,
        verified: tuple[VerifiedFixCommand, ...],
        errors: tuple[str, ...],
    ) -> FixitResult:
        """Return a new result with verified suggestions."""
        return FixitResult(
            context=self.context,
            analysis=self.analysis,
            verified_suggestions=verified,
            verification_errors=errors,
            actions=self.actions,
            outcome=self.outcome,
            incident_id=self.incident_id,
            used_fallback=self.used_fallback,
        )

    def with_action(self, action: FixitAction) -> FixitResult:
        """Return a new result with an additional action."""
        return FixitResult(
            context=self.context,
            analysis=self.analysis,
            verified_suggestions=self.verified_suggestions,
            verification_errors=self.verification_errors,
            actions=(*self.actions, action),
            outcome=self.outcome,
            incident_id=self.incident_id,
            used_fallback=self.used_fallback,
        )

    def with_outcome(self, outcome: FixitOutcome) -> FixitResult:
        """Return a new result with the given outcome."""
        return FixitResult(
            context=self.context,
            analysis=self.analysis,
            verified_suggestions=self.verified_suggestions,
            verification_errors=self.verification_errors,
            actions=self.actions,
            outcome=outcome,
            incident_id=self.incident_id,
            used_fallback=self.used_fallback,
        )

    def with_incident_id(self, incident_id: str) -> FixitResult:
        """Return a new result with the given incident ID."""
        return FixitResult(
            context=self.context,
            analysis=self.analysis,
            verified_suggestions=self.verified_suggestions,
            verification_errors=self.verification_errors,
            actions=self.actions,
            outcome=self.outcome,
            incident_id=incident_id,
            used_fallback=self.used_fallback,
        )

    @property
    def has_suggestions(self) -> bool:
        """Check if there are any verified suggestions."""
        return len(self.verified_suggestions) > 0

    @property
    def safe_suggestions(self) -> tuple[VerifiedFixCommand, ...]:
        """Get only the suggestions that passed verification."""
        return tuple(s for s in self.verified_suggestions if s.is_safe)
