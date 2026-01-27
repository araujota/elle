"""Incident comparison and diffing.

Provides structural comparison between two incidents, enabling users
to understand how incidents evolved or differ from each other.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from elle.daemon.incidents.models import (
    IncidentReport,
)


class DecisionDiff(BaseModel):
    """Comparison of decisions between two incidents."""

    model_config = ConfigDict(frozen=True)

    approach_changed: bool = False
    """Whether the chosen approach differs."""

    approach_1: str = ""
    approach_2: str = ""

    rationale_diff: str = ""
    """Summary of rationale differences."""

    confidence_delta: float = 0.0
    """Difference in confidence (incident2 - incident1)."""

    sources_added: tuple[str, ...] = Field(default_factory=tuple)
    """Provenance sources in incident2 but not incident1."""

    sources_removed: tuple[str, ...] = Field(default_factory=tuple)
    """Provenance sources in incident1 but not incident2."""


class SnapshotDiff(BaseModel):
    """Comparison of system snapshots."""

    model_config = ConfigDict(frozen=True)

    memory_delta_mb: int = 0
    """Change in available memory."""

    disk_pressure_delta: float = 0.0
    """Change in disk pressure."""

    cpu_load_delta: float = 0.0
    """Change in CPU load (1min avg)."""

    services_started: tuple[str, ...] = Field(default_factory=tuple)
    """Services that started between snapshots."""

    services_stopped: tuple[str, ...] = Field(default_factory=tuple)
    """Services that stopped between snapshots."""

    containers_started: tuple[str, ...] = Field(default_factory=tuple)
    """Containers that started."""

    containers_stopped: tuple[str, ...] = Field(default_factory=tuple)
    """Containers that stopped."""

    packages_installed: tuple[str, ...] = Field(default_factory=tuple)
    """Packages installed."""

    packages_removed: tuple[str, ...] = Field(default_factory=tuple)
    """Packages removed."""


class ActionDiff(BaseModel):
    """Comparison of actions between incidents."""

    model_config = ConfigDict(frozen=True)

    actions_in_1_only: tuple[str, ...] = Field(default_factory=tuple)
    """Actions only in incident1."""

    actions_in_2_only: tuple[str, ...] = Field(default_factory=tuple)
    """Actions only in incident2."""

    actions_in_both: tuple[str, ...] = Field(default_factory=tuple)
    """Actions common to both."""

    success_rate_1: float = 0.0
    """Success rate of actions in incident1."""

    success_rate_2: float = 0.0
    """Success rate of actions in incident2."""


class IncidentDiff(BaseModel):
    """Complete diff between two incidents."""

    model_config = ConfigDict(frozen=True)

    incident1_id: str
    incident2_id: str

    # Timeline
    time_delta: timedelta = Field(default=timedelta())
    """Time between incidents (incident2.created_at - incident1.created_at)."""

    incident1_created: datetime
    incident2_created: datetime

    # Classification comparison
    same_domain: bool = True
    domain1: str = ""
    domain2: str = ""

    same_severity: bool = True
    severity1: str = ""
    severity2: str = ""

    status1: str = ""
    status2: str = ""

    # Title/summary comparison
    title_similarity: float = 0.0
    """Similarity score for titles (0-1)."""

    titles_match: bool = False
    title1: str = ""
    title2: str = ""

    # Symptoms comparison
    symptoms_added: tuple[str, ...] = Field(default_factory=tuple)
    """Symptoms in incident2 but not incident1."""

    symptoms_removed: tuple[str, ...] = Field(default_factory=tuple)
    """Symptoms in incident1 but not incident2."""

    symptoms_common: tuple[str, ...] = Field(default_factory=tuple)
    """Symptoms in both incidents."""

    # Cause comparison
    causes_added: tuple[str, ...] = Field(default_factory=tuple)
    causes_removed: tuple[str, ...] = Field(default_factory=tuple)
    causes_common: tuple[str, ...] = Field(default_factory=tuple)

    root_cause1: str | None = None
    root_cause2: str | None = None
    root_cause_match: bool = False

    # Decision comparison
    decision_diff: DecisionDiff | None = None

    # Outcome comparison
    outcome1: str = "unknown"
    outcome2: str = "unknown"
    outcome_improved: bool = False
    """Whether incident2 had a better outcome."""

    # Action comparison
    action_diff: ActionDiff | None = None

    # Fingerprint comparison
    fingerprint_similarity: float = 0.0
    """Similarity of incident fingerprints (0-1)."""

    # Entities
    entities_added: tuple[str, ...] = Field(default_factory=tuple)
    entities_removed: tuple[str, ...] = Field(default_factory=tuple)
    entities_common: tuple[str, ...] = Field(default_factory=tuple)


class IncidentDiffer:
    """Compare two incidents structurally."""

    @staticmethod
    def diff(incident1: IncidentReport, incident2: IncidentReport) -> IncidentDiff:
        """Compare two incidents and produce a structured diff.

        Args:
            incident1: First incident (usually older).
            incident2: Second incident (usually newer).

        Returns:
            IncidentDiff with comparison results.
        """
        # Timeline
        time_delta = incident2.created_at - incident1.created_at

        # Domains
        same_domain = incident1.domain == incident2.domain

        # Severity
        same_severity = incident1.severity == incident2.severity

        # Titles
        titles_match = incident1.title.lower() == incident2.title.lower()
        title_similarity = IncidentDiffer._string_similarity(incident1.title, incident2.title)

        # Symptoms
        symptoms1 = set(incident1.symptoms)
        symptoms2 = set(incident2.symptoms)
        symptoms_added = tuple(symptoms2 - symptoms1)
        symptoms_removed = tuple(symptoms1 - symptoms2)
        symptoms_common = tuple(symptoms1 & symptoms2)

        # Causes
        causes1 = set(incident1.suspected_causes)
        causes2 = set(incident2.suspected_causes)
        causes_added = tuple(causes2 - causes1)
        causes_removed = tuple(causes1 - causes2)
        causes_common = tuple(causes1 & causes2)

        root_cause_match = (
            incident1.root_cause is not None
            and incident2.root_cause is not None
            and incident1.root_cause.lower() == incident2.root_cause.lower()
        )

        # Decision comparison
        decision_diff = IncidentDiffer._compare_decisions(incident1, incident2)

        # Outcome comparison
        outcome_rank = {"unknown": 0, "worse": 1, "no_change": 2, "partial": 3, "improved": 4}
        outcome_improved = outcome_rank.get(incident2.outcome, 0) > outcome_rank.get(incident1.outcome, 0)

        # Fingerprint similarity
        fingerprint_similarity = IncidentDiffer._compare_fingerprints(
            incident1.fingerprint, incident2.fingerprint
        )

        # Entities
        entities1 = set(incident1.fingerprint.entities)
        entities2 = set(incident2.fingerprint.entities)
        entities_added = tuple(entities2 - entities1)
        entities_removed = tuple(entities1 - entities2)
        entities_common = tuple(entities1 & entities2)

        return IncidentDiff(
            incident1_id=incident1.incident_id,
            incident2_id=incident2.incident_id,
            time_delta=time_delta,
            incident1_created=incident1.created_at,
            incident2_created=incident2.created_at,
            same_domain=same_domain,
            domain1=incident1.domain,
            domain2=incident2.domain,
            same_severity=same_severity,
            severity1=incident1.severity,
            severity2=incident2.severity,
            status1=incident1.status,
            status2=incident2.status,
            title_similarity=title_similarity,
            titles_match=titles_match,
            title1=incident1.title,
            title2=incident2.title,
            symptoms_added=symptoms_added,
            symptoms_removed=symptoms_removed,
            symptoms_common=symptoms_common,
            causes_added=causes_added,
            causes_removed=causes_removed,
            causes_common=causes_common,
            root_cause1=incident1.root_cause,
            root_cause2=incident2.root_cause,
            root_cause_match=root_cause_match,
            decision_diff=decision_diff,
            outcome1=incident1.outcome,
            outcome2=incident2.outcome,
            outcome_improved=outcome_improved,
            fingerprint_similarity=fingerprint_similarity,
            entities_added=entities_added,
            entities_removed=entities_removed,
            entities_common=entities_common,
        )

    @staticmethod
    def _compare_decisions(inc1: IncidentReport, inc2: IncidentReport) -> DecisionDiff:
        """Compare decision records between incidents."""
        dr1 = inc1.decision_record
        dr2 = inc2.decision_record

        approach1 = dr1.chosen_approach if dr1 else inc1.decision.get("approach", "")
        approach2 = dr2.chosen_approach if dr2 else inc2.decision.get("approach", "")

        confidence1 = (
            dr1.confidence.total_confidence()
            if dr1 and hasattr(dr1.confidence, "total_confidence")
            else 0.0
        )
        confidence2 = (
            dr2.confidence.total_confidence()
            if dr2 and hasattr(dr2.confidence, "total_confidence")
            else 0.0
        )

        # Compare provenance sources
        sources1: set[str] = set()
        sources2: set[str] = set()

        if dr1 and dr1.provenance:
            if dr1.provenance.man_pages:
                sources1.update(f"man:{mp.name}" for mp in dr1.provenance.man_pages)
            if dr1.provenance.prior_incidents:
                sources1.update(f"incident:{pi.incident_id[:8]}" for pi in dr1.provenance.prior_incidents)

        if dr2 and dr2.provenance:
            if dr2.provenance.man_pages:
                sources2.update(f"man:{mp.name}" for mp in dr2.provenance.man_pages)
            if dr2.provenance.prior_incidents:
                sources2.update(f"incident:{pi.incident_id[:8]}" for pi in dr2.provenance.prior_incidents)

        return DecisionDiff(
            approach_changed=approach1.lower() != approach2.lower(),
            approach_1=approach1,
            approach_2=approach2,
            confidence_delta=confidence2 - confidence1,
            sources_added=tuple(sources2 - sources1),
            sources_removed=tuple(sources1 - sources2),
        )

    @staticmethod
    def _compare_fingerprints(fp1: Any, fp2: Any) -> float:
        """Calculate fingerprint similarity (0-1)."""
        if fp1 is None or fp2 is None:
            return 0.0

        # Compare numeric fields
        numeric_fields = [
            ("disk_pressure", 1.0),
            ("mem_pressure", 1.0),
            ("swap_pressure", 1.0),
            ("cpu_pressure", 5.0),  # Normalize to 0-1 assuming max of 5
        ]

        total_diff = 0.0
        for field, max_val in numeric_fields:
            v1 = getattr(fp1, field, 0.0)
            v2 = getattr(fp2, field, 0.0)
            if max_val > 0:
                total_diff += abs(v1 - v2) / max_val

        # Normalize to 0-1 similarity
        avg_diff = total_diff / len(numeric_fields)
        similarity = max(0.0, 1.0 - avg_diff)

        # Also consider entity overlap
        entities1 = set(fp1.entities) if hasattr(fp1, "entities") else set()
        entities2 = set(fp2.entities) if hasattr(fp2, "entities") else set()

        if entities1 or entities2:
            entity_overlap = len(entities1 & entities2) / max(len(entities1 | entities2), 1)
            similarity = (similarity + entity_overlap) / 2

        return similarity

    @staticmethod
    def _string_similarity(s1: str, s2: str) -> float:
        """Calculate string similarity using simple character overlap."""
        if not s1 and not s2:
            return 1.0
        if not s1 or not s2:
            return 0.0

        s1_lower = s1.lower()
        s2_lower = s2.lower()

        if s1_lower == s2_lower:
            return 1.0

        # Use word overlap
        words1 = set(s1_lower.split())
        words2 = set(s2_lower.split())

        if not words1 or not words2:
            return 0.0

        overlap = len(words1 & words2)
        total = len(words1 | words2)

        return overlap / total if total > 0 else 0.0


def render_incident_diff(diff: IncidentDiff) -> str:
    """Render an incident diff as formatted text.

    Args:
        diff: The incident diff to render.

    Returns:
        Formatted diff string.
    """
    lines = []

    # Header
    lines.append(f"INCIDENT DIFF: {diff.incident1_id[:8]}... vs {diff.incident2_id[:8]}...")
    lines.append("=" * 60)
    lines.append("")

    # Timeline
    delta_days = diff.time_delta.days
    delta_hours = diff.time_delta.seconds // 3600
    if delta_days > 0:
        time_str = f"{delta_days}d {delta_hours}h"
    elif delta_hours > 0:
        time_str = f"{delta_hours}h"
    else:
        time_str = f"{diff.time_delta.seconds // 60}m"

    lines.append(f"Time between: {time_str}")
    lines.append(f"  Incident 1: {diff.incident1_created.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"  Incident 2: {diff.incident2_created.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # Classification
    lines.append("CLASSIFICATION:")
    if diff.same_domain:
        lines.append(f"  Domain: {diff.domain1}")
    else:
        lines.append(f"  Domain: {diff.domain1} -> {diff.domain2}")

    if diff.same_severity:
        lines.append(f"  Severity: {diff.severity1}")
    else:
        lines.append(f"  Severity: {diff.severity1} -> {diff.severity2}")

    lines.append(f"  Status: {diff.status1} -> {diff.status2}")
    lines.append("")

    # Titles
    if diff.titles_match:
        lines.append(f"TITLE: {diff.title1}")
    else:
        lines.append("TITLES:")
        lines.append(f"  1: {diff.title1}")
        lines.append(f"  2: {diff.title2}")
        lines.append(f"  Similarity: {diff.title_similarity:.0%}")
    lines.append("")

    # Symptoms
    if diff.symptoms_added or diff.symptoms_removed:
        lines.append("SYMPTOMS CHANGED:")
        for s in diff.symptoms_added:
            lines.append(f"  + {s}")
        for s in diff.symptoms_removed:
            lines.append(f"  - {s}")
        lines.append("")

    # Causes
    if diff.causes_added or diff.causes_removed:
        lines.append("SUSPECTED CAUSES CHANGED:")
        for c in diff.causes_added:
            lines.append(f"  + {c}")
        for c in diff.causes_removed:
            lines.append(f"  - {c}")
        lines.append("")

    # Root cause
    if diff.root_cause1 or diff.root_cause2:
        lines.append("ROOT CAUSE:")
        if diff.root_cause_match:
            lines.append(f"  {diff.root_cause1}")
        else:
            lines.append(f"  1: {diff.root_cause1 or '(not determined)'}")
            lines.append(f"  2: {diff.root_cause2 or '(not determined)'}")
        lines.append("")

    # Decision
    if diff.decision_diff:
        dd = diff.decision_diff
        if dd.approach_changed:
            lines.append("APPROACH CHANGED:")
            lines.append(f"  1: {dd.approach_1 or '(none)'}")
            lines.append(f"  2: {dd.approach_2 or '(none)'}")
        if dd.confidence_delta != 0:
            sign = "+" if dd.confidence_delta > 0 else ""
            lines.append(f"  Confidence: {sign}{dd.confidence_delta:.2f}")
        if dd.sources_added:
            lines.append(f"  Sources added: {', '.join(dd.sources_added)}")
        if dd.sources_removed:
            lines.append(f"  Sources removed: {', '.join(dd.sources_removed)}")
        if dd.approach_changed or dd.sources_added or dd.sources_removed:
            lines.append("")

    # Outcome
    lines.append("OUTCOME:")
    lines.append(f"  1: {diff.outcome1}")
    lines.append(f"  2: {diff.outcome2}")
    if diff.outcome_improved:
        lines.append("  [IMPROVED]")
    lines.append("")

    # Fingerprint
    lines.append(f"FINGERPRINT SIMILARITY: {diff.fingerprint_similarity:.0%}")

    # Entities
    if diff.entities_added or diff.entities_removed:
        lines.append("")
        lines.append("ENTITIES CHANGED:")
        for e in diff.entities_added:
            lines.append(f"  + {e}")
        for e in diff.entities_removed:
            lines.append(f"  - {e}")

    return "\n".join(lines)
