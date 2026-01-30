"""Unified retrieval pipeline for the agentic system.

Runs parallel retrieval across multiple sources:
- Incident Vault: Similar past incidents and their outcomes
- Man Vault: Documentation and command syntax
- Capability Registry: Typed operations matching the request

Results are combined into a RetrievalContext that provides the LLM
with all relevant prior art, documentation, and available actions.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from elle.cli.agentic.unified_input import AgenticInput

logger = logging.getLogger(__name__)


# =============================================================================
# Models
# =============================================================================


class ManSnippet(BaseModel):
    """A snippet from Man Vault search."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Command/topic name")
    section: str = Field(description="Man section (1, 5, 8, etc.)")
    snippet: str = Field(description="Relevant documentation snippet")
    score: float = Field(description="Relevance score")
    match_section: str | None = Field(
        default=None,
        description="Section where match was found (OPTIONS, DESCRIPTION, etc.)",
    )


class IncidentMatch(BaseModel):
    """A matched incident from the Incident Vault."""

    model_config = ConfigDict(frozen=True)

    incident_id: str = Field(description="Unique incident ID")
    title: str = Field(description="Incident title")
    summary: str = Field(description="Brief summary of what happened")
    domain: str = Field(description="Incident domain (net, disk, docker, etc.)")
    outcome: str = Field(description="Outcome: resolved, partial, unresolved, workaround")
    outcome_weight: float = Field(description="Quality weight of outcome (0-1)")
    similarity_score: float = Field(description="Similarity to current query")
    days_ago: int = Field(description="How many days ago this occurred")

    # For learning from what worked
    root_cause: str | None = Field(default=None, description="Identified root cause")
    decision: str | None = Field(default=None, description="Decision made")
    successful_actions: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple,
        description="Actions that succeeded",
    )


class CapabilityMatch(BaseModel):
    """A matched capability from semantic search."""

    model_config = ConfigDict(frozen=True)

    capability_name: str = Field(description="Capability name (e.g., service.restart)")
    domain: str = Field(description="Capability domain")
    description: str = Field(description="What this capability does")
    risk_level: str = Field(description="Risk level")
    match_score: float = Field(description="Relevance score")
    success_rate: float = Field(description="Historical success rate")
    is_approved: bool = Field(description="Whether approved for use")
    source_command: str | None = Field(default=None, description="Source command")


class RetrievalContext(BaseModel):
    """Combined context from all retrieval sources.

    This is the primary input for LLM reasoning in the agentic loop.
    """

    model_config = ConfigDict(frozen=True)

    # Original input
    input: AgenticInput = Field(description="The original agentic input")

    # Retrieved content
    prior_incidents: tuple[IncidentMatch, ...] = Field(
        default_factory=tuple,
        description="Similar past incidents with outcomes",
    )
    man_vault_snippets: tuple[ManSnippet, ...] = Field(
        default_factory=tuple,
        description="Relevant documentation snippets",
    )
    capability_matches: tuple[CapabilityMatch, ...] = Field(
        default_factory=tuple,
        description="Capabilities matching the request",
    )

    # Metadata
    retrieval_duration_ms: int = Field(
        default=0,
        description="Time taken for retrieval",
    )
    sources_queried: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Which sources were queried",
    )

    @property
    def has_prior_art(self) -> bool:
        """Check if we have any relevant prior incidents."""
        return len(self.prior_incidents) > 0

    @property
    def has_documentation(self) -> bool:
        """Check if we have any relevant documentation."""
        return len(self.man_vault_snippets) > 0

    @property
    def has_capabilities(self) -> bool:
        """Check if we have any matching capabilities."""
        return len(self.capability_matches) > 0

    @property
    def best_prior_incident(self) -> IncidentMatch | None:
        """Get the best matching prior incident."""
        if self.prior_incidents:
            return max(
                self.prior_incidents,
                key=lambda i: i.similarity_score * i.outcome_weight,
            )
        return None

    def format_for_prompt(self, max_chars: int = 6000) -> str:
        """Format retrieval context for inclusion in LLM prompt.

        Args:
            max_chars: Maximum characters to include.

        Returns:
            Formatted context string.
        """
        parts: list[str] = []
        used_chars = 0

        # Prior incidents (most valuable for learning)
        if self.prior_incidents:
            parts.append("## Similar Past Incidents\n")
            for i, inc in enumerate(self.prior_incidents[:3], 1):
                section = f"### {i}. {inc.title} ({inc.days_ago} days ago, {inc.outcome})\n"
                section += f"Summary: {inc.summary}\n"
                if inc.root_cause:
                    section += f"Root Cause: {inc.root_cause}\n"
                if inc.decision:
                    section += f"Decision: {inc.decision}\n"
                if inc.successful_actions:
                    section += "What worked:\n"
                    for action in inc.successful_actions[:3]:
                        cmd = action.get("command", "N/A")
                        section += f"  - {cmd}\n"
                section += "\n"

                if used_chars + len(section) < max_chars:
                    parts.append(section)
                    used_chars += len(section)

        # Capabilities (for execution)
        if self.capability_matches:
            cap_section = "## Available Capabilities\n"
            for cap in self.capability_matches[:5]:
                cap_line = f"- **{cap.capability_name}** ({cap.domain}): {cap.description[:80]}"
                if cap.risk_level in ("high", "critical"):
                    cap_line += f" [risk: {cap.risk_level}]"
                cap_line += "\n"
                cap_section += cap_line

            if used_chars + len(cap_section) < max_chars:
                parts.append(cap_section)
                used_chars += len(cap_section)

        # Documentation (reference)
        if self.man_vault_snippets:
            doc_section = "## Documentation\n"
            for snippet in self.man_vault_snippets[:3]:
                snippet_text = f"**{snippet.name}({snippet.section})**"
                if snippet.match_section:
                    snippet_text += f" [{snippet.match_section}]"
                snippet_text += f":\n{snippet.snippet[:500]}\n\n"

                if used_chars + len(snippet_text) < max_chars:
                    doc_section += snippet_text
                    used_chars += len(snippet_text)

            parts.append(doc_section)

        return "\n".join(parts)


# =============================================================================
# Pipeline Implementation
# =============================================================================


class UnifiedRetrievalPipeline:
    """Parallel multi-source retrieval pipeline.

    Queries all sources concurrently and combines results:
    1. Incident Vault - for prior art and what worked
    2. Man Vault - for documentation and syntax
    3. Capability Registry - for available operations

    Usage:
        pipeline = UnifiedRetrievalPipeline()
        context = await pipeline.retrieve(input)
    """

    def __init__(
        self,
        incident_k: int = 3,
        man_vault_k: int = 5,
        capability_k: int = 5,
    ) -> None:
        """Initialize the pipeline.

        Args:
            incident_k: Max incidents to retrieve.
            man_vault_k: Max man vault snippets.
            capability_k: Max capabilities to retrieve.
        """
        self.incident_k = incident_k
        self.man_vault_k = man_vault_k
        self.capability_k = capability_k

    async def retrieve(self, input: AgenticInput) -> RetrievalContext:
        """Run parallel retrieval across all sources.

        Args:
            input: The agentic input to retrieve context for.

        Returns:
            RetrievalContext with all retrieved content.
        """
        start_time = time.time()
        query = input.query_text

        if not query:
            return RetrievalContext(
                input=input,
                retrieval_duration_ms=0,
                sources_queried=(),
            )

        # Run all searches in parallel with timeout
        # Default timeout of 30 seconds prevents hanging on slow sources
        retrieval_timeout = 30.0
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    self._search_incidents(query, input.domain_hint),
                    self._search_man_vault(query),
                    self._search_capabilities(query, input.domain_hint),
                    return_exceptions=True,
                ),
                timeout=retrieval_timeout,
            )
        except TimeoutError:
            logger.warning(f"Retrieval pipeline timed out after {retrieval_timeout}s")
            results = ((), (), ())

        # Unpack results, handling exceptions
        incidents = results[0] if isinstance(results[0], tuple) else ()
        man_snippets = results[1] if isinstance(results[1], tuple) else ()
        capabilities = results[2] if isinstance(results[2], tuple) else ()

        # Track which sources were queried
        sources = []
        if not isinstance(results[0], Exception):
            sources.append("incidents")
        if not isinstance(results[1], Exception):
            sources.append("man_vault")
        if not isinstance(results[2], Exception):
            sources.append("capabilities")

        duration_ms = int((time.time() - start_time) * 1000)

        return RetrievalContext(
            input=input,
            prior_incidents=incidents,
            man_vault_snippets=man_snippets,
            capability_matches=capabilities,
            retrieval_duration_ms=duration_ms,
            sources_queried=tuple(sources),
        )

    async def _search_incidents(
        self,
        query: str,
        domain: str | None,
    ) -> tuple[IncidentMatch, ...]:
        """Search the Incident Vault for similar past incidents."""
        try:
            from elle.daemon.incidents.retriever import search

            results = search(
                query=query,
                domain=domain,
                k=self.incident_k,
                search_type="hybrid",
            )

            matches: list[IncidentMatch] = []
            for r in results:
                incident = r.incident

                # Get successful actions if available
                successful_actions: list[dict[str, Any]] = []
                if hasattr(incident, "actions") and incident.actions:
                    successful_actions = [
                        a.model_dump() if hasattr(a, "model_dump") else dict(a)
                        for a in incident.actions
                        if getattr(a, "success", False)
                    ]

                matches.append(
                    IncidentMatch(
                        incident_id=incident.incident_id,
                        title=incident.title,
                        summary=incident.summary or "",
                        domain=incident.domain,
                        outcome=incident.outcome or "unknown",
                        outcome_weight=r.outcome_weight,
                        similarity_score=r.score,
                        days_ago=(datetime.now(timezone.utc) - incident.updated_at).days,
                        root_cause=getattr(incident, "root_cause", None),
                        decision=getattr(incident, "decision", None),
                        successful_actions=tuple(successful_actions),
                    )
                )

            return tuple(matches)

        except Exception as e:
            logger.warning(f"Incident search failed: {e}")
            return ()

    async def _search_man_vault(
        self,
        query: str,
    ) -> tuple[ManSnippet, ...]:
        """Search the Man Vault for documentation."""
        try:
            from elle.daemon.manvault.retriever import search

            results = search(
                query=query,
                k=self.man_vault_k,
                search_type="hybrid",
            )

            snippets: list[ManSnippet] = []
            for r in results:
                snippets.append(
                    ManSnippet(
                        name=r.name,
                        section=r.section,
                        snippet=r.snippet,
                        score=r.score,
                        match_section=getattr(r, "match_section", None),
                    )
                )

            return tuple(snippets)

        except Exception as e:
            logger.warning(f"Man vault search failed: {e}")
            return ()

    async def _search_capabilities(
        self,
        query: str,
        domain: str | None,
    ) -> tuple[CapabilityMatch, ...]:
        """Search for matching capabilities."""
        try:
            from elle.capabilities.autogen.retriever import search_capabilities

            results = search_capabilities(
                query=query,
                domain=domain,
                k=self.capability_k,
                search_type="hybrid",
            )

            matches: list[CapabilityMatch] = []
            for r in results:
                matches.append(
                    CapabilityMatch(
                        capability_name=r.capability_name,
                        domain=r.domain,
                        description=r.description,
                        risk_level=r.risk_level,
                        match_score=r.score,
                        success_rate=r.success_rate,
                        is_approved=r.is_approved,
                        source_command=r.source_command,
                    )
                )

            return tuple(matches)

        except Exception as e:
            logger.warning(f"Capability search failed: {e}")
            return ()

    async def retrieve_for_incident(
        self,
        incident_id: str,
    ) -> RetrievalContext:
        """Retrieve context for a specific incident.

        Args:
            incident_id: ID of the incident to handle.

        Returns:
            RetrievalContext with incident-specific content.
        """
        try:
            from elle.daemon.incidents.store import get_incident

            incident = get_incident(incident_id)
            if not incident:
                return RetrievalContext(
                    input=AgenticInput.from_incident(incident_id),
                    retrieval_duration_ms=0,
                )

            # Build query from incident
            query_parts = [incident.title]
            if incident.summary:
                query_parts.append(incident.summary)
            query = " ".join(query_parts)

            # Create input from incident
            input = AgenticInput.from_incident(
                incident_id,
                auto_triggered=False,
            )
            # Override with actual query text
            input = AgenticInput(
                input_id=input.input_id,
                source=input.source,
                timestamp=input.timestamp,
                incident_id=incident_id,
                user_message=query,  # Use incident text as query
                domain_hint=incident.domain,
            )

            return await self.retrieve(input)

        except Exception as e:
            logger.error(f"Failed to retrieve for incident: {e}")
            return RetrievalContext(
                input=AgenticInput.from_incident(incident_id),
                retrieval_duration_ms=0,
            )


# =============================================================================
# Module-level pipeline instance
# =============================================================================

_pipeline: UnifiedRetrievalPipeline | None = None


def get_retrieval_pipeline() -> UnifiedRetrievalPipeline:
    """Get the shared retrieval pipeline instance.

    Returns:
        UnifiedRetrievalPipeline instance.
    """
    global _pipeline
    if _pipeline is None:
        _pipeline = UnifiedRetrievalPipeline()
    return _pipeline


def reset_retrieval_pipeline() -> None:
    """Reset the shared pipeline (for testing)."""
    global _pipeline
    _pipeline = None


async def retrieve_context(input: AgenticInput) -> RetrievalContext:
    """Convenience function to retrieve context for an input.

    Args:
        input: AgenticInput to retrieve context for.

    Returns:
        RetrievalContext with all retrieved content.
    """
    pipeline = get_retrieval_pipeline()
    return await pipeline.retrieve(input)
