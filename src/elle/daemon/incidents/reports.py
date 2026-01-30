"""Report generation for incidents and efficacy.

Generates human-readable reports from incidents with full audit trail,
efficacy summaries, and trend analysis.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from types import TracebackType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from elle.daemon.incidents.efficacy import (
    DomainEfficacy,
    EfficacyStats,
)
from elle.daemon.incidents.efficacy_tracker import (
    get_all_domain_efficacy,
    get_efficacy_stats,
)
from elle.daemon.incidents.models import (
    IncidentAction,
    IncidentReport,
)
from elle.daemon.incidents.narrative import Narrative
from elle.daemon.incidents.schema import ensure_schema, get_connection

# =============================================================================
# Report Models
# =============================================================================


ReportFormat = Literal["markdown", "json", "text"]


class IncidentReportSummary(BaseModel):
    """Summary of a single incident for reports."""

    model_config = ConfigDict(frozen=True)

    incident_id: str
    title: str
    domain: str
    severity: str
    status: str
    outcome: str
    created_at: datetime
    time_to_resolve_sec: int | None
    action_count: int
    confidence: float
    has_narrative: bool


class IncidentFullReport(BaseModel):
    """Full incident report with all details."""

    model_config = ConfigDict(frozen=True)

    # Core incident
    incident: IncidentReport

    # Actions taken
    actions: tuple[IncidentAction, ...]

    # Narrative (if generated)
    narrative: Narrative | None

    # Similar incidents referenced
    similar_incidents: tuple[IncidentReportSummary, ...]

    # Generated report content
    report_text: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    format: ReportFormat = "markdown"


class EfficacyReport(BaseModel):
    """Efficacy report showing success rates and trends."""

    model_config = ConfigDict(frozen=True)

    # Stats
    stats: EfficacyStats

    # Report content
    report_text: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    format: ReportFormat = "markdown"


class TrendReport(BaseModel):
    """Trend report showing system patterns over time."""

    model_config = ConfigDict(frozen=True)

    # Time range
    from_time: datetime
    to_time: datetime

    # Incident trends
    total_incidents: int
    by_domain: dict[str, int]
    by_outcome: dict[str, int]
    avg_time_to_resolve_sec: float | None

    # Efficacy trends
    domain_trends: tuple[DomainEfficacy, ...]

    # Report content
    report_text: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# Report Generator
# =============================================================================


class ReportGenerator:
    """Generates reports from incidents and efficacy data."""

    def __init__(self, conn: sqlite3.Connection | None = None):
        """Initialize the report generator.

        Args:
            conn: SQLite connection. Creates new connection if not provided.
        """
        self._own_conn = conn is None
        self._conn = conn if conn else get_connection()
        if self._own_conn:
            ensure_schema(self._conn)

    def close(self) -> None:
        """Close the connection if we own it."""
        if self._own_conn and self._conn:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> ReportGenerator:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    # -------------------------------------------------------------------------
    # Incident Reports
    # -------------------------------------------------------------------------

    def generate_incident_report(
        self,
        incident_id: str,
        format: ReportFormat = "markdown",
    ) -> IncidentFullReport:
        """Generate full incident report with audit trail.

        Args:
            incident_id: The incident UUID.
            format: Output format (markdown, json, text).

        Returns:
            IncidentFullReport with rendered content.

        Raises:
            ValueError: If incident not found.
        """
        from elle.daemon.incidents.store import (
            get_actions,
            get_incident,
        )

        # Get incident
        incident = get_incident(incident_id, conn=self._conn)
        if not incident:
            raise ValueError(f"Incident not found: {incident_id}")

        # Get actions
        actions = tuple(get_actions(incident_id, conn=self._conn))

        # Get narrative (if exists)
        narrative = self._get_narrative(incident_id)

        # Get similar incidents
        similar = self._get_similar_incidents(incident)

        # Generate report text
        if format == "markdown":
            report_text = self._render_incident_markdown(incident, actions, narrative, similar)
        elif format == "json":
            report_text = self._render_incident_json(incident, actions, narrative, similar)
        else:
            report_text = self._render_incident_text(incident, actions, narrative, similar)

        return IncidentFullReport(
            incident=incident,
            actions=actions,
            narrative=narrative,
            similar_incidents=similar,
            report_text=report_text,
            format=format,
        )

    def _get_narrative(self, incident_id: str) -> Narrative | None:
        """Get narrative for an incident if it exists.

        Returns None if the narratives table doesn't exist yet or
        if no narrative has been generated for this incident.
        """
        import json
        import sqlite3

        from elle.daemon.incidents.narrative import CausalChain

        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT chain_json, summary, timeline_json, explanation, keywords_json,
                       generated_at, model_used
                FROM narratives
                WHERE incident_id = ?
                ORDER BY generated_at DESC
                LIMIT 1
                """,
                (incident_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            chain_data = json.loads(row["chain_json"]) if row["chain_json"] else {}
            chain = CausalChain(**chain_data) if chain_data else CausalChain(chain_id="")
            timeline = tuple(json.loads(row["timeline_json"])) if row["timeline_json"] else ()
            keywords = tuple(json.loads(row["keywords_json"])) if row["keywords_json"] else ()

            return Narrative(
                chain=chain,
                summary=row["summary"] or "",
                timeline=timeline,
                explanation=row["explanation"] or "",
                keywords=keywords,
                generated_at=datetime.fromisoformat(row["generated_at"]) if row["generated_at"] else datetime.utcnow(),
                model_used=row["model_used"] or "",
            )
        except sqlite3.OperationalError:
            # Table doesn't exist yet - that's OK
            return None
        except Exception:
            return None

    def _get_similar_incidents(
        self,
        incident: IncidentReport,
        limit: int = 5,
    ) -> tuple[IncidentReportSummary, ...]:
        """Get similar incidents based on domain and entities."""
        from elle.daemon.incidents.store import list_incidents

        # Find incidents in same domain
        similar = []
        domain_incidents = list_incidents(
            domain=incident.domain,
            status="resolved",
            limit=limit * 2,
            conn=self._conn,
        )

        for inc in domain_incidents:
            if inc.incident_id != incident.incident_id:
                # Count overlapping entities
                overlap = len(set(inc.fingerprint.entities) & set(incident.fingerprint.entities))
                if overlap > 0 or inc.domain == incident.domain:
                    similar.append(
                        IncidentReportSummary(
                            incident_id=inc.incident_id,
                            title=inc.title,
                            domain=inc.domain,
                            severity=inc.severity,
                            status=inc.status,
                            outcome=inc.outcome,
                            created_at=inc.created_at,
                            time_to_resolve_sec=inc.time_to_resolve_sec,
                            action_count=0,  # Would need to query
                            confidence=inc.confidence,
                            has_narrative=False,
                        )
                    )

        return tuple(similar[:limit])

    def _render_incident_markdown(
        self,
        incident: IncidentReport,
        actions: tuple[IncidentAction, ...],
        narrative: Narrative | None,
        similar: tuple[IncidentReportSummary, ...],
    ) -> str:
        """Render incident report as Markdown."""
        lines = []

        # Header
        lines.append(f"# Incident Report: {incident.title}")
        lines.append("")
        lines.append(f"**ID:** `{incident.incident_id}`")
        lines.append(f"**Created:** {incident.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        lines.append(f"**Domain:** {incident.domain}")
        lines.append(f"**Severity:** {incident.severity}")
        lines.append(f"**Status:** {incident.status}")
        lines.append(f"**Outcome:** {incident.outcome}")
        lines.append("")

        # Summary
        if incident.summary:
            lines.append("## Summary")
            lines.append("")
            lines.append(incident.summary)
            lines.append("")

        # Narrative (if available)
        if narrative:
            lines.append("## Causal Narrative")
            lines.append("")
            lines.append(narrative.summary)
            lines.append("")
            if narrative.timeline:
                lines.append("### Timeline")
                lines.append("")
                for item in narrative.timeline:
                    lines.append(f"- {item}")
                lines.append("")
            if narrative.explanation:
                lines.append("### Detailed Explanation")
                lines.append("")
                lines.append(narrative.explanation)
                lines.append("")

        # Symptoms
        if incident.symptoms:
            lines.append("## Symptoms")
            lines.append("")
            for symptom in incident.symptoms:
                lines.append(f"- {symptom}")
            lines.append("")

        # Root Cause
        if incident.root_cause:
            lines.append("## Root Cause")
            lines.append("")
            lines.append(incident.root_cause)
            lines.append("")
        elif incident.suspected_causes:
            lines.append("## Suspected Causes")
            lines.append("")
            for cause in incident.suspected_causes:
                lines.append(f"- {cause}")
            lines.append("")

        # Actions Taken
        if actions:
            lines.append("## Actions Taken")
            lines.append("")
            lines.append("| Step | Type | Command | Success |")
            lines.append("|------|------|---------|---------|")
            for action in actions:
                cmd = (
                    action.command[:50] + "..."
                    if action.command and len(action.command) > 50
                    else (action.command or "-")
                )
                success = "Yes" if action.success else "No"
                lines.append(f"| {action.step_index + 1} | {action.kind} | `{cmd}` | {success} |")
            lines.append("")

        # Verification
        if incident.verification_steps:
            lines.append("## Verification Steps")
            lines.append("")
            for step in incident.verification_steps:
                lines.append(f"- {step}")
            lines.append("")

        # Metrics
        if incident.time_to_resolve_sec:
            minutes = incident.time_to_resolve_sec // 60
            seconds = incident.time_to_resolve_sec % 60
            lines.append("## Resolution Metrics")
            lines.append("")
            lines.append(f"- **Time to resolve:** {minutes}m {seconds}s")
            lines.append(f"- **Actions taken:** {len(actions)}")
            lines.append(f"- **Confidence:** {incident.confidence:.0%}")
            lines.append("")

        # Provenance
        if incident.decision_record and incident.decision_record.provenance:
            prov = incident.decision_record.provenance
            lines.append("## Decision Provenance")
            lines.append("")
            lines.append(prov.summary())
            lines.append("")
            if prov.man_pages:
                lines.append("### Man Page References")
                lines.append("")
                for mp in prov.man_pages:
                    lines.append(f"- `{mp.name}({mp.section})`: {mp.snippet[:100]}...")
                lines.append("")
            if prov.prior_incidents:
                lines.append("### Prior Incidents Referenced")
                lines.append("")
                for pi in prov.prior_incidents:
                    lines.append(f"- [{pi.title}] (outcome: {pi.outcome}, similarity: {pi.similarity_score:.0%})")
                lines.append("")

        # Similar Incidents
        if similar:
            lines.append("## Similar Past Incidents")
            lines.append("")
            for s in similar:
                lines.append(f"- **{s.title}** ({s.domain}) - {s.outcome}")
            lines.append("")

        # Footer
        lines.append("---")
        lines.append(f"*Generated at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}*")

        return "\n".join(lines)

    def _render_incident_json(
        self,
        incident: IncidentReport,
        actions: tuple[IncidentAction, ...],
        narrative: Narrative | None,
        similar: tuple[IncidentReportSummary, ...],
    ) -> str:
        """Render incident report as JSON."""
        import json

        data = {
            "incident": incident.model_dump(mode="json"),
            "actions": [a.model_dump(mode="json") for a in actions],
            "narrative": narrative.model_dump(mode="json") if narrative else None,
            "similar_incidents": [s.model_dump(mode="json") for s in similar],
            "generated_at": datetime.utcnow().isoformat(),
        }
        return json.dumps(data, indent=2, default=str)

    def _render_incident_text(
        self,
        incident: IncidentReport,
        actions: tuple[IncidentAction, ...],
        narrative: Narrative | None,
        similar: tuple[IncidentReportSummary, ...],
    ) -> str:
        """Render incident report as plain text."""
        lines = []

        lines.append("=" * 60)
        lines.append(f"INCIDENT REPORT: {incident.title}")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"ID:       {incident.incident_id}")
        lines.append(f"Created:  {incident.created_at}")
        lines.append(f"Domain:   {incident.domain}")
        lines.append(f"Severity: {incident.severity}")
        lines.append(f"Status:   {incident.status}")
        lines.append(f"Outcome:  {incident.outcome}")
        lines.append("")

        if incident.summary:
            lines.append("SUMMARY:")
            lines.append("-" * 40)
            lines.append(incident.summary)
            lines.append("")

        if narrative:
            lines.append("CAUSAL NARRATIVE:")
            lines.append("-" * 40)
            lines.append(narrative.summary)
            lines.append("")

        if actions:
            lines.append("ACTIONS TAKEN:")
            lines.append("-" * 40)
            for action in actions:
                status = "OK" if action.success else "FAIL"
                lines.append(f"  [{status}] {action.kind}: {action.command or '-'}")
            lines.append("")

        if incident.root_cause:
            lines.append("ROOT CAUSE:")
            lines.append("-" * 40)
            lines.append(f"  {incident.root_cause}")
            lines.append("")

        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # Efficacy Reports
    # -------------------------------------------------------------------------

    def generate_efficacy_report(
        self,
        domain: str | None = None,
        format: ReportFormat = "markdown",
    ) -> EfficacyReport:
        """Generate efficacy report showing success rates.

        Args:
            domain: Filter to specific domain (or all if None).
            format: Output format.

        Returns:
            EfficacyReport with rendered content.
        """
        stats = get_efficacy_stats(conn=self._conn)

        if format == "markdown":
            report_text = self._render_efficacy_markdown(stats, domain)
        elif format == "json":
            report_text = self._render_efficacy_json(stats, domain)
        else:
            report_text = self._render_efficacy_text(stats, domain)

        return EfficacyReport(
            stats=stats,
            report_text=report_text,
            format=format,
        )

    def _render_efficacy_markdown(
        self,
        stats: EfficacyStats,
        domain: str | None,
    ) -> str:
        """Render efficacy report as Markdown."""
        lines = []

        lines.append("# ELLE Efficacy Report")
        lines.append("")
        lines.append(f"*Generated: {stats.computed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}*")
        lines.append("")

        # Overall stats
        lines.append("## Overall Performance")
        lines.append("")
        lines.append(f"- **Total Finalized Incidents:** {stats.total_finalized_incidents}")
        lines.append(f"- **Overall Success Rate:** {stats.overall_success_rate:.1%}")
        lines.append("")

        # Domain breakdown
        if stats.domain_stats:
            lines.append("## Success Rates by Domain")
            lines.append("")
            lines.append("| Domain | Incidents | Success Rate | Improved | Partial | No Change | Worse |")
            lines.append("|--------|-----------|--------------|----------|---------|-----------|-------|")

            domain_stats = stats.domain_stats
            if domain:
                domain_stats = tuple(d for d in domain_stats if d.domain == domain)

            for d in domain_stats:
                lines.append(
                    f"| {d.domain} | {d.total_incidents} | {d.success_rate:.1%} | "
                    f"{d.improved_count} | {d.partial_count} | {d.no_change_count} | {d.worse_count} |"
                )
            lines.append("")

        # Top entities
        if stats.top_entities:
            lines.append("## Top Entities (by incident count)")
            lines.append("")
            lines.append("| Entity | Incidents | Success Rate |")
            lines.append("|--------|-----------|--------------|")
            for e in stats.top_entities[:10]:
                lines.append(f"| {e.entity} | {e.total_incidents} | {e.success_rate:.1%} |")
            lines.append("")

        # Top approaches
        if stats.top_approaches:
            lines.append("## Most Effective Approaches")
            lines.append("")
            lines.append("| Domain | Commands | Uses | Success Rate | Avg Time |")
            lines.append("|--------|----------|------|--------------|----------|")
            for a in stats.top_approaches[:10]:
                cmds = ", ".join(a.key_commands[:2]) if a.key_commands else "-"
                if len(a.key_commands) > 2:
                    cmds += f" (+{len(a.key_commands) - 2})"
                avg_time = f"{a.avg_time_to_resolve_sec // 60}m" if a.avg_time_to_resolve_sec else "-"
                lines.append(f"| {a.domain} | {cmds} | {a.total_uses} | {a.success_rate:.1%} | {avg_time} |")
            lines.append("")

        # Insights
        lines.append("## Insights")
        lines.append("")
        if stats.domain_stats:
            best = max(stats.domain_stats, key=lambda d: d.success_rate)
            worst = min(stats.domain_stats, key=lambda d: d.success_rate)
            if best.success_rate > 0.7:
                lines.append(f"- **Best performing domain:** {best.domain} ({best.success_rate:.1%})")
            if worst.success_rate < 0.5 and worst.total_incidents >= 3:
                lines.append(f"- **Needs attention:** {worst.domain} ({worst.success_rate:.1%})")

        return "\n".join(lines)

    def _render_efficacy_json(
        self,
        stats: EfficacyStats,
        domain: str | None,
    ) -> str:
        """Render efficacy report as JSON."""
        import json

        data = stats.model_dump(mode="json")
        if domain:
            data["domain_stats"] = [d for d in data.get("domain_stats", []) if d.get("domain") == domain]
        return json.dumps(data, indent=2, default=str)

    def _render_efficacy_text(
        self,
        stats: EfficacyStats,
        domain: str | None,
    ) -> str:
        """Render efficacy report as plain text."""
        lines = []

        lines.append("=" * 50)
        lines.append("ELLE EFFICACY REPORT")
        lines.append("=" * 50)
        lines.append("")
        lines.append(f"Total Incidents:    {stats.total_finalized_incidents}")
        lines.append(f"Overall Success:    {stats.overall_success_rate:.1%}")
        lines.append("")
        lines.append("BY DOMAIN:")
        lines.append("-" * 50)

        domain_stats = stats.domain_stats
        if domain:
            domain_stats = tuple(d for d in domain_stats if d.domain == domain)

        for d in domain_stats:
            lines.append(f"  {d.domain:12} {d.total_incidents:4} incidents  {d.success_rate:.1%}")
        lines.append("")

        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # Trend Reports
    # -------------------------------------------------------------------------

    def generate_trend_report(
        self,
        days: int = 7,
        format: ReportFormat = "markdown",
    ) -> TrendReport:
        """Generate trend report for the specified time range.

        Args:
            days: Number of days to analyze.
            format: Output format.

        Returns:
            TrendReport with rendered content.
        """
        from elle.daemon.incidents.store import list_incidents

        to_time = datetime.utcnow()
        from_time = to_time - timedelta(days=days)

        # Get incidents in range
        incidents = list_incidents(limit=10000, conn=self._conn)
        incidents_in_range = [i for i in incidents if i.created_at >= from_time]

        # Aggregate by domain
        by_domain: dict[str, int] = {}
        for inc in incidents_in_range:
            by_domain[inc.domain] = by_domain.get(inc.domain, 0) + 1

        # Aggregate by outcome
        by_outcome: dict[str, int] = {}
        for inc in incidents_in_range:
            by_outcome[inc.outcome] = by_outcome.get(inc.outcome, 0) + 1

        # Compute average time to resolve
        resolve_times = [inc.time_to_resolve_sec for inc in incidents_in_range if inc.time_to_resolve_sec]
        avg_resolve = sum(resolve_times) / len(resolve_times) if resolve_times else None

        # Get domain trends
        domain_trends = tuple(get_all_domain_efficacy(conn=self._conn))

        # Generate report text
        if format == "markdown":
            report_text = self._render_trend_markdown(
                from_time, to_time, incidents_in_range, by_domain, by_outcome, avg_resolve, domain_trends
            )
        else:
            report_text = self._render_trend_text(
                from_time, to_time, incidents_in_range, by_domain, by_outcome, avg_resolve, domain_trends
            )

        return TrendReport(
            from_time=from_time,
            to_time=to_time,
            total_incidents=len(incidents_in_range),
            by_domain=by_domain,
            by_outcome=by_outcome,
            avg_time_to_resolve_sec=avg_resolve,
            domain_trends=domain_trends,
            report_text=report_text,
        )

    def _render_trend_markdown(
        self,
        from_time: datetime,
        to_time: datetime,
        incidents: list[Any],
        by_domain: dict[str, int],
        by_outcome: dict[str, int],
        avg_resolve: float | None,
        domain_trends: tuple[DomainEfficacy, ...],
    ) -> str:
        """Render trend report as Markdown."""
        lines = []

        lines.append("# ELLE Trend Report")
        lines.append("")
        lines.append(f"**Period:** {from_time.strftime('%Y-%m-%d')} to {to_time.strftime('%Y-%m-%d')}")
        lines.append("")

        # Summary
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- **Total Incidents:** {len(incidents)}")
        if avg_resolve:
            lines.append(f"- **Average Resolution Time:** {avg_resolve / 60:.1f} minutes")
        lines.append("")

        # By domain
        if by_domain:
            lines.append("## Incidents by Domain")
            lines.append("")
            for domain, count in sorted(by_domain.items(), key=lambda x: -x[1]):
                lines.append(f"- **{domain}:** {count}")
            lines.append("")

        # By outcome
        if by_outcome:
            lines.append("## Incidents by Outcome")
            lines.append("")
            for outcome, count in sorted(by_outcome.items(), key=lambda x: -x[1]):
                lines.append(f"- **{outcome}:** {count}")
            lines.append("")

        # Domain performance trends
        if domain_trends:
            lines.append("## Domain Performance")
            lines.append("")
            lines.append("| Domain | Success Rate | Recent Activity |")
            lines.append("|--------|--------------|-----------------|")
            for d in domain_trends:
                days_since = (to_time - d.last_updated).days
                activity = "Active" if days_since < 7 else f"{days_since}d ago"
                lines.append(f"| {d.domain} | {d.success_rate:.1%} | {activity} |")
            lines.append("")

        return "\n".join(lines)

    def _render_trend_text(
        self,
        from_time: datetime,
        to_time: datetime,
        incidents: list[Any],
        by_domain: dict[str, int],
        by_outcome: dict[str, int],
        avg_resolve: float | None,
        domain_trends: tuple[DomainEfficacy, ...],
    ) -> str:
        """Render trend report as plain text."""
        lines = []

        lines.append("=" * 50)
        lines.append("ELLE TREND REPORT")
        lines.append("=" * 50)
        lines.append(f"Period: {from_time.strftime('%Y-%m-%d')} to {to_time.strftime('%Y-%m-%d')}")
        lines.append("")
        lines.append(f"Total Incidents: {len(incidents)}")
        if avg_resolve:
            lines.append(f"Avg Resolution:  {avg_resolve / 60:.1f} minutes")
        lines.append("")

        if by_domain:
            lines.append("BY DOMAIN:")
            for domain, count in sorted(by_domain.items(), key=lambda x: -x[1]):
                lines.append(f"  {domain:12} {count}")
            lines.append("")

        return "\n".join(lines)


# =============================================================================
# Convenience functions
# =============================================================================


def generate_incident_report(
    incident_id: str,
    format: ReportFormat = "markdown",
    conn: sqlite3.Connection | None = None,
) -> str:
    """Generate a full incident report.

    Args:
        incident_id: The incident UUID.
        format: Output format.
        conn: SQLite connection.

    Returns:
        Rendered report text.
    """
    with ReportGenerator(conn) as gen:
        report = gen.generate_incident_report(incident_id, format)
        return report.report_text


def generate_efficacy_report(
    domain: str | None = None,
    format: ReportFormat = "markdown",
    conn: sqlite3.Connection | None = None,
) -> str:
    """Generate an efficacy report.

    Args:
        domain: Filter to specific domain.
        format: Output format.
        conn: SQLite connection.

    Returns:
        Rendered report text.
    """
    with ReportGenerator(conn) as gen:
        report = gen.generate_efficacy_report(domain, format)
        return report.report_text


def generate_trend_report(
    days: int = 7,
    format: ReportFormat = "markdown",
    conn: sqlite3.Connection | None = None,
) -> str:
    """Generate a trend report.

    Args:
        days: Number of days to analyze.
        format: Output format.
        conn: SQLite connection.

    Returns:
        Rendered report text.
    """
    with ReportGenerator(conn) as gen:
        report = gen.generate_trend_report(days, format)
        return report.report_text
