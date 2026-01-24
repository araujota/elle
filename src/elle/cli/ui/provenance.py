"""Provenance display utilities for UI panels.

Converts incident provenance models to UI-compatible formats
for rendering in rationale panels.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    from elle.daemon.incidents.models import (
        ConfidenceBreakdown,
        DecisionRecord,
        IncidentCitation,
        ManPageCitation,
        Provenance,
    )


def man_citations_to_ui(
    citations: Sequence["ManPageCitation"],
) -> list[dict[str, str | float]]:
    """Convert ManPageCitation models to UI-compatible dicts.

    Args:
        citations: Sequence of ManPageCitation models.

    Returns:
        List of dicts with 'name', 'section', 'snippet', 'relevance_score'.
    """
    return [
        {
            "name": c.name,
            "section": c.section,
            "snippet": c.snippet,
            "relevance_score": c.relevance_score,
            "match_section": c.match_section or "",
        }
        for c in citations
    ]


def incident_citations_to_ui(
    citations: Sequence["IncidentCitation"],
) -> list[dict[str, Any]]:
    """Convert IncidentCitation models to UI-compatible dicts.

    Args:
        citations: Sequence of IncidentCitation models.

    Returns:
        List of dicts with 'title', 'outcome', 'similarity_score', 'successful_actions'.
    """
    return [
        {
            "incident_id": c.incident_id,
            "title": c.title,
            "outcome": c.outcome,
            "similarity_score": c.similarity_score,
            "match_type": c.match_type,
            "successful_actions": list(c.successful_actions),
        }
        for c in citations
    ]


def confidence_to_ui(
    confidence: "ConfidenceBreakdown",
) -> dict[str, float]:
    """Convert ConfidenceBreakdown model to UI-compatible dict.

    Args:
        confidence: ConfidenceBreakdown model.

    Returns:
        Dict with 'overall', 'from_man_vault', 'from_incident_vault', 'from_llm'.
    """
    return {
        "overall": confidence.overall,
        "from_man_vault": confidence.from_man_vault,
        "from_incident_vault": confidence.from_incident_vault,
        "from_telemetry": confidence.from_telemetry,
        "from_llm": confidence.from_llm,
        "dominant_tier": confidence.dominant_tier,
    }


def provenance_to_ui(
    provenance: "Provenance",
) -> dict[str, Any]:
    """Convert Provenance model to UI-compatible dict.

    Args:
        provenance: Provenance model.

    Returns:
        Dict with 'man_citations', 'incident_citations', 'summary', 'primary_source'.
    """
    return {
        "man_citations": man_citations_to_ui(provenance.man_pages),
        "incident_citations": incident_citations_to_ui(provenance.prior_incidents),
        "summary": provenance.summary(),
        "primary_source": provenance.primary_source,
    }


def decision_record_to_ui(
    record: "DecisionRecord",
) -> dict[str, Any]:
    """Convert DecisionRecord model to UI-compatible dict for display.

    Args:
        record: DecisionRecord model.

    Returns:
        Dict with all fields needed for rationale panel rendering.
    """
    provenance_data = provenance_to_ui(record.provenance)
    confidence_data = confidence_to_ui(record.confidence)

    return {
        "chosen_approach": record.chosen_approach,
        "rationale": record.rationale,
        "man_citations": provenance_data["man_citations"],
        "incident_citations": provenance_data["incident_citations"],
        "confidence": confidence_data,
        "rationale_summary": provenance_data["summary"],
        "planned_commands": list(record.planned_commands),
    }


def build_rationale_kwargs(
    record: "DecisionRecord | None" = None,
    provenance: "Provenance | None" = None,
    confidence: "ConfidenceBreakdown | None" = None,
) -> dict[str, Any]:
    """Build keyword arguments for plan_panel/fixit_panel rationale display.

    This is the main interface for passing provenance data to UI panels.
    Accepts either a complete DecisionRecord or individual components.

    Args:
        record: Complete DecisionRecord (if available).
        provenance: Provenance model (if DecisionRecord not available).
        confidence: ConfidenceBreakdown model (if DecisionRecord not available).

    Returns:
        Dict of kwargs to pass to plan_panel or fixit_panel.

    Example:
        from elle.cli.ui.provenance import build_rationale_kwargs
        from elle.cli.ui.panels import plan_panel

        kwargs = build_rationale_kwargs(decision_record)
        panel = plan_panel(
            title="Restart nginx",
            explanation="...",
            steps=[...],
            **kwargs,
        )
    """
    if record is not None:
        data = decision_record_to_ui(record)
        return {
            "man_citations": data["man_citations"],
            "incident_citations": data["incident_citations"],
            "confidence": data["confidence"],
            "rationale_summary": data["rationale_summary"],
            "show_rationale": True,
        }

    result: dict[str, Any] = {"show_rationale": True}

    if provenance is not None:
        prov_data = provenance_to_ui(provenance)
        result["man_citations"] = prov_data["man_citations"]
        result["incident_citations"] = prov_data["incident_citations"]
        result["rationale_summary"] = prov_data["summary"]

    if confidence is not None:
        result["confidence"] = confidence_to_ui(confidence)

    # Only show rationale if we have actual content
    has_content = (
        result.get("man_citations")
        or result.get("incident_citations")
        or result.get("confidence")
    )
    result["show_rationale"] = bool(has_content)

    return result
