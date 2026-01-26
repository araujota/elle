"""Narrative generation from causal chains.

Uses LLM synthesis to convert causal chains into
human-readable narratives explaining what happened
and why.
"""

import re
import sqlite3
from datetime import datetime
from typing import Any

from elle.common.pydantic_compat import safe_model_dump
from elle.daemon.incidents.narrative import (
    CausalChain,
    CausalLink,
    Narrative,
)
from elle.daemon.incidents.schema import ensure_schema, get_connection

# Schema additions for storing narratives
NARRATIVES_TABLE = """
CREATE TABLE IF NOT EXISTS narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id TEXT UNIQUE NOT NULL,
    incident_id TEXT,
    summary TEXT NOT NULL,
    timeline_json TEXT NOT NULL DEFAULT '[]',
    explanation TEXT NOT NULL DEFAULT '',
    keywords_json TEXT NOT NULL DEFAULT '[]',
    chain_json TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    model_used TEXT NOT NULL DEFAULT ''
)
"""

NARRATIVES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_narratives_incident ON narratives(incident_id)
"""


def _ensure_narrative_schema(conn: sqlite3.Connection) -> None:
    """Ensure narrative tables exist."""
    cursor = conn.cursor()
    cursor.execute(NARRATIVES_TABLE)
    cursor.execute(NARRATIVES_INDEX)
    conn.commit()


def _json_dumps(obj: Any) -> str:
    """Serialize to JSON."""
    import json
    return json.dumps(obj, default=str)


def _json_loads(s: str | None) -> Any:
    """Parse JSON."""
    import json
    if not s:
        return []
    return json.loads(s)


class NarrativeBuilder:
    """Builds human-readable narratives from causal chains.

    Uses LLM synthesis when available, with rule-based
    fallback for offline operation.
    """

    def __init__(
        self,
        use_llm: bool = True,
        model: str | None = None,
    ):
        """Initialize the narrative builder.

        Args:
            use_llm: Whether to use LLM for synthesis.
            model: LLM model to use (uses default if not specified).
        """
        self.use_llm = use_llm
        self.model = model

    def build_narrative(
        self,
        chain: CausalChain,
        incident_id: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> Narrative:
        """Build a narrative from a causal chain.

        Args:
            chain: The causal chain to narrate.
            incident_id: Associated incident ID (optional).
            conn: SQLite connection for storing the narrative.

        Returns:
            Generated Narrative.
        """
        # Build timeline from chain
        timeline = self._build_timeline(chain)

        # Generate summary and explanation
        if self.use_llm:
            summary, explanation, model_used = self._generate_with_llm(chain, timeline)
        else:
            summary = self._generate_summary_rule_based(chain, timeline)
            explanation = self._generate_explanation_rule_based(chain, timeline)
            model_used = "rule-based"

        # Extract keywords
        keywords = self._extract_keywords(chain, summary, explanation)

        narrative = Narrative(
            chain=chain,
            summary=summary,
            timeline=tuple(timeline),
            explanation=explanation,
            keywords=tuple(keywords),
            generated_at=datetime.utcnow(),
            model_used=model_used,
        )

        # Store if connection provided
        if conn is not None:
            self._store_narrative(narrative, incident_id, conn)

        return narrative

    def _build_timeline(self, chain: CausalChain) -> list[str]:
        """Build chronological timeline from chain links."""
        timeline: list[str] = []

        for link in chain.links:
            # Format timestamp
            cause_time = link.cause_timestamp.strftime("%H:%M:%S")
            effect_time = link.effect_timestamp.strftime("%H:%M:%S")

            # Create timeline entry
            if link.relationship == "triggered":
                entry = f"[{cause_time}] {link.cause_summary} → triggered → {link.effect_summary}"
            elif link.relationship == "contributed_to":
                entry = f"[{cause_time}] {link.cause_summary} → contributed to → [{effect_time}] {link.effect_summary}"
            elif link.relationship == "preceded":
                entry = f"[{cause_time}] {link.cause_summary} (preceded) [{effect_time}] {link.effect_summary}"
            else:
                entry = f"[{cause_time}] {link.cause_summary} ↔ [{effect_time}] {link.effect_summary}"

            timeline.append(entry)

        return timeline

    def _generate_with_llm(
        self,
        chain: CausalChain,
        timeline: list[str],
    ) -> tuple[str, str, str]:
        """Generate summary and explanation using LLM."""
        try:
            from elle.rag.llm import get_llm

            llm = get_llm()
            if not llm.is_available():
                # Fall back to rule-based
                return (
                    self._generate_summary_rule_based(chain, timeline),
                    self._generate_explanation_rule_based(chain, timeline),
                    "unavailable",
                )

            # Build prompt
            prompt = self._build_llm_prompt(chain, timeline)

            # Generate with LLM
            response = llm.generate(
                prompt,
                temperature=0.3,
                max_tokens=500,
            )

            # Parse response
            summary, explanation = self._parse_llm_response(response)

            model_used = self.model or llm.model_name

            return summary, explanation, model_used

        except Exception:
            # Fall back to rule-based on any error
            return (
                self._generate_summary_rule_based(chain, timeline),
                self._generate_explanation_rule_based(chain, timeline),
                "fallback",
            )

    def _build_llm_prompt(
        self,
        chain: CausalChain,
        timeline: list[str],
    ) -> str:
        """Build prompt for LLM narrative generation."""
        timeline_text = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(timeline))

        # Extract key entities and domains
        entities: set[str] = set()
        _domains: set[str] = set()  # Reserved for future domain-specific prompting
        for link in chain.links:
            # Extract entities from summaries
            entities.update(self._extract_entities_from_text(link.cause_summary))
            entities.update(self._extract_entities_from_text(link.effect_summary))

        prompt = f"""Analyze this sequence of system events and write a clear explanation of what happened.

Timeline:
{timeline_text}

Overall confidence in this chain: {chain.overall_confidence:.0%}
Entities involved: {', '.join(entities) if entities else 'Unknown'}

Write your response in two parts:

SUMMARY: (2-3 sentences explaining what happened in plain language)

EXPLANATION: (Detailed explanation with evidence from the timeline, explaining the causal chain step by step)

Focus on:
- What was the root cause?
- How did it cascade into the final symptom?
- What evidence supports this conclusion?
"""
        return prompt

    def _parse_llm_response(self, response: str) -> tuple[str, str]:
        """Parse LLM response into summary and explanation."""
        summary = ""
        explanation = ""

        # Try to parse structured response
        if "SUMMARY:" in response:
            parts = response.split("SUMMARY:", 1)
            if len(parts) > 1:
                after_summary = parts[1]
                if "EXPLANATION:" in after_summary:
                    summary_part, explanation_part = after_summary.split("EXPLANATION:", 1)
                    summary = summary_part.strip()
                    explanation = explanation_part.strip()
                else:
                    summary = after_summary.strip()
        else:
            # No structure, use first paragraph as summary
            paragraphs = response.strip().split("\n\n")
            if paragraphs:
                summary = paragraphs[0].strip()
                if len(paragraphs) > 1:
                    explanation = "\n\n".join(paragraphs[1:]).strip()

        return summary, explanation

    def _generate_summary_rule_based(
        self,
        chain: CausalChain,
        timeline: list[str],
    ) -> str:
        """Generate summary using rules (no LLM)."""
        if not chain.links:
            return "Unable to determine causal chain."

        first_link = chain.links[0]
        last_link = chain.links[-1]

        # Build summary from chain endpoints
        summary = f"{last_link.effect_summary} "

        if first_link.cause_summary != last_link.effect_summary:
            summary += f"was caused by {first_link.cause_summary}. "

        if len(chain.links) > 1:
            summary += f"This involved a chain of {len(chain.links)} related events "
            summary += f"over {self._format_duration(chain)}."

        return summary.strip()

    def _generate_explanation_rule_based(
        self,
        chain: CausalChain,
        timeline: list[str],
    ) -> str:
        """Generate explanation using rules (no LLM)."""
        if not chain.links:
            return "Insufficient data to explain the causal chain."

        lines = ["The following sequence of events occurred:"]
        lines.append("")

        for i, link in enumerate(chain.links, 1):
            relationship_text = {
                "triggered": "directly triggered",
                "contributed_to": "contributed to",
                "preceded": "occurred before",
                "correlated": "was correlated with",
            }.get(link.relationship, "was related to")

            line = f"{i}. {link.cause_summary} {relationship_text} {link.effect_summary}"

            if link.temporal_delta_sec > 0:
                line += f" ({self._format_seconds(link.temporal_delta_sec)} later)"

            if link.confidence >= 0.8:
                line += " [high confidence]"
            elif link.confidence < 0.5:
                line += " [low confidence]"

            lines.append(line)

        lines.append("")
        lines.append(f"Overall chain confidence: {chain.overall_confidence:.0%}")

        return "\n".join(lines)

    def _extract_keywords(
        self,
        chain: CausalChain,
        summary: str,
        explanation: str,
    ) -> list[str]:
        """Extract keywords from chain and narratives."""
        keywords: set[str] = set()

        # Extract from summaries in chain
        for link in chain.links:
            keywords.update(self._extract_keywords_from_text(link.cause_summary))
            keywords.update(self._extract_keywords_from_text(link.effect_summary))

        # Extract from narrative text
        keywords.update(self._extract_keywords_from_text(summary))
        keywords.update(self._extract_keywords_from_text(explanation))

        return sorted(keywords)

    def _extract_keywords_from_text(self, text: str) -> set[str]:
        """Extract technical keywords from text."""
        keywords: set[str] = set()

        patterns = [
            r'\b(failed|error|timeout|refused|denied|full|exhausted)\b',
            r'\b(nginx|apache|mysql|postgres|redis|docker|systemd)\b',
            r'\b(disk|memory|cpu|network|socket|port)\b',
            r'\b(service|process|container|mount|partition)\b',
            r'\b(OOM|SMART|I/O|DNS|SSH|HTTP)\b',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            keywords.update(m.lower() for m in matches)

        return keywords

    def _extract_entities_from_text(self, text: str) -> set[str]:
        """Extract entity names from text."""
        entities: set[str] = set()

        patterns = [
            r'([a-zA-Z0-9_-]+)\.service',
            r'(eth\d+|wlan\d+|enp\d+s\d+|ens\d+)',
            r'(/dev/\w+)',
            r'(/[a-zA-Z0-9/_-]+)',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text)
            entities.update(matches)

        return entities

    def _format_duration(self, chain: CausalChain) -> str:
        """Format chain duration as human-readable string."""
        if not chain.earliest_timestamp or not chain.latest_timestamp:
            return "unknown duration"

        delta = chain.latest_timestamp - chain.earliest_timestamp
        seconds = int(delta.total_seconds())
        return self._format_seconds(seconds)

    def _format_seconds(self, seconds: int) -> str:
        """Format seconds as human-readable duration."""
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            minutes = seconds // 60
            return f"{minutes}m"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m" if minutes else f"{hours}h"

    def _store_narrative(
        self,
        narrative: Narrative,
        incident_id: str | None,
        conn: sqlite3.Connection,
    ) -> None:
        """Store narrative in database."""
        _ensure_narrative_schema(conn)

        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO narratives (
                chain_id, incident_id, summary, timeline_json,
                explanation, keywords_json, chain_json,
                generated_at, model_used
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                narrative.chain.chain_id,
                incident_id,
                narrative.summary,
                _json_dumps(list(narrative.timeline)),
                narrative.explanation,
                _json_dumps(list(narrative.keywords)),
                _json_dumps(safe_model_dump(narrative.chain)),
                narrative.generated_at.isoformat(),
                narrative.model_used,
            ),
        )
        conn.commit()


def get_narrative(
    chain_id: str,
    conn: sqlite3.Connection | None = None,
) -> Narrative | None:
    """Retrieve a stored narrative by chain ID.

    Args:
        chain_id: The chain ID to look up.
        conn: SQLite connection.

    Returns:
        Narrative if found, None otherwise.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)
        _ensure_narrative_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM narratives WHERE chain_id = ?",
            (chain_id,),
        )
        row = cursor.fetchone()

        if not row:
            return None

        # Parse chain from JSON
        chain_data = _json_loads(row["chain_json"])

        # Reconstruct CausalLink objects
        links = []
        for link_data in chain_data.get("links", []):
            link_data["cause_timestamp"] = datetime.fromisoformat(link_data["cause_timestamp"])
            link_data["effect_timestamp"] = datetime.fromisoformat(link_data["effect_timestamp"])
            link_data["evidence"] = tuple(link_data.get("evidence", []))
            links.append(CausalLink(**link_data))

        # Reconstruct root_cause if present
        root_cause = None
        if chain_data.get("root_cause"):
            rc_data = chain_data["root_cause"]
            rc_data["cause_timestamp"] = datetime.fromisoformat(rc_data["cause_timestamp"])
            rc_data["effect_timestamp"] = datetime.fromisoformat(rc_data["effect_timestamp"])
            rc_data["evidence"] = tuple(rc_data.get("evidence", []))
            root_cause = CausalLink(**rc_data)

        chain = CausalChain(
            chain_id=chain_data["chain_id"],
            root_cause=root_cause,
            links=tuple(links),
            final_symptom=chain_data.get("final_symptom", ""),
            overall_confidence=chain_data.get("overall_confidence", 0.0),
            search_iterations=chain_data.get("search_iterations", 0),
            earliest_timestamp=datetime.fromisoformat(chain_data["earliest_timestamp"]) if chain_data.get("earliest_timestamp") else None,
            latest_timestamp=datetime.fromisoformat(chain_data["latest_timestamp"]) if chain_data.get("latest_timestamp") else None,
        )

        return Narrative(
            chain=chain,
            summary=row["summary"],
            timeline=tuple(_json_loads(row["timeline_json"])),
            explanation=row["explanation"],
            keywords=tuple(_json_loads(row["keywords_json"])),
            generated_at=datetime.fromisoformat(row["generated_at"]),
            model_used=row["model_used"],
        )

    finally:
        if own_conn:
            conn.close()


def get_narratives_for_incident(
    incident_id: str,
    conn: sqlite3.Connection | None = None,
) -> list[Narrative]:
    """Get all narratives associated with an incident.

    Args:
        incident_id: The incident ID.
        conn: SQLite connection.

    Returns:
        List of Narratives.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)
        _ensure_narrative_schema(conn)

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT chain_id FROM narratives WHERE incident_id = ?",
            (incident_id,),
        )

        narratives = []
        for row in cursor.fetchall():
            narrative = get_narrative(row["chain_id"], conn)
            if narrative:
                narratives.append(narrative)

        return narratives

    finally:
        if own_conn:
            conn.close()


# =============================================================================
# Integration with incident correlator
# =============================================================================


def generate_narrative_for_incident(
    incident_id: str,
    conn: sqlite3.Connection | None = None,
) -> Narrative | None:
    """Generate a narrative for an incident.

    Performs multi-hop search from the incident's events
    and generates a causal narrative.

    Args:
        incident_id: The incident to generate narrative for.
        conn: SQLite connection.

    Returns:
        Generated Narrative, or None if unable to build chain.
    """
    from elle.daemon.incidents.multihop import MultiHopSearch
    from elle.daemon.incidents.store import get_incident

    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        # Get incident
        incident = get_incident(incident_id, conn=conn)
        if not incident:
            return None

        # Build query from incident
        query = f"{incident.title} {incident.summary}"
        if incident.symptoms:
            query += " " + " ".join(incident.symptoms[:3])

        # Perform multi-hop search
        searcher = MultiHopSearch()
        result = searcher.search(query, conn=conn)

        if not result.best_chain:
            return None

        # Generate narrative
        builder = NarrativeBuilder()
        narrative = builder.build_narrative(
            result.best_chain,
            incident_id=incident_id,
            conn=conn,
        )

        return narrative

    finally:
        if own_conn:
            conn.close()
