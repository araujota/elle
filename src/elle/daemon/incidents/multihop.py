"""Multi-hop search for building causal chains.

Implements iterative search that expands from an initial query
or event to discover related events, incidents, and causal
relationships.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Any, cast

from elle.common.pydantic_compat import safe_model_dump
from elle.daemon.incidents.models import (
    IncidentReport,
    IncidentSearchResult,
)
from elle.daemon.incidents.narrative import (
    CausalChain,
    CausalLink,
    CauseType,
    EffectType,
    MultiHopConfig,
    MultiHopResult,
    Relationship,
    SearchHop,
)
from elle.daemon.incidents.retriever import search as incident_search


def _parse_datetime(s: str) -> datetime:
    """Parse ISO format string to datetime."""
    return datetime.fromisoformat(s)


class MultiHopSearch:
    """Multi-hop search algorithm for building causal chains.

    Iteratively searches incidents and events, expanding the search
    based on discovered entities and keywords to build a complete
    causal narrative.
    """

    def __init__(
        self,
        config: MultiHopConfig | None = None,
    ):
        """Initialize the multi-hop search.

        Args:
            config: Search configuration. Uses defaults if not provided.
        """
        self.config = config or MultiHopConfig()

    def search(
        self,
        query: str,
        initial_event: dict[str, Any] | None = None,
    ) -> MultiHopResult:
        """Perform multi-hop search to build causal chains.

        Iteration 1 (Broad):
          - Search Incident Vault (hybrid)
          - Search Telemetry Events (time window, entity)
          - Extract entities, keywords, time bounds

        Iteration 2+ (Contextual Expansion):
          - For high-relevance results: search related entities
          - Keyword extraction -> new search terms
          - Temporal expansion: look earlier for causes

        Stop conditions:
          - Reached time window limit
          - No new high-relevance results
          - Confidence exceeds threshold
          - Max iterations reached

        Args:
            query: Initial search query.
            initial_event: Starting event if available.

        Returns:
            MultiHopResult with discovered chains and search metadata.
        """
        start_time = datetime.utcnow()
        hops: list[SearchHop] = []
        all_incidents: dict[str, IncidentSearchResult] = {}
        all_events: dict[str, dict[str, Any]] = {}
        discovered_entities: set[str] = set()
        discovered_keywords: set[str] = set()

        # Set initial time bounds
        if initial_event:
            event_time = self._get_event_time(initial_event)
            time_end = event_time + timedelta(hours=1)
            time_start = event_time - timedelta(hours=self.config.time_window_hours)
            all_events[initial_event.get("event_id", "")] = initial_event
        else:
            time_end = datetime.utcnow()
            time_start = time_end - timedelta(hours=self.config.time_window_hours)

        current_query = query
        iteration = 0

        while iteration < self.config.max_iterations:
            # Perform search hop
            hop = self._search_hop(
                iteration=iteration,
                query=current_query,
                time_start=time_start,
                time_end=time_end,
                known_entities=discovered_entities,
            )
            hops.append(hop)

            # Collect results
            hop_incidents = self._search_incidents(
                current_query,
                time_start,
                time_end,
            )
            for inc in hop_incidents:
                if inc.incident.incident_id not in all_incidents:
                    all_incidents[inc.incident.incident_id] = inc

            hop_events = self._search_events(
                current_query,
                time_start,
                time_end,
                entities=list(discovered_entities)[:5],
            )
            for evt in hop_events:
                evt_id = evt.get("event_id", "")
                if evt_id and evt_id not in all_events:
                    all_events[evt_id] = evt

            # Update discovered entities and keywords
            new_entities = set(hop.entities_discovered) - discovered_entities
            new_keywords = set(hop.keywords_extracted) - discovered_keywords
            discovered_entities.update(new_entities)
            discovered_keywords.update(new_keywords)

            # Check stop conditions
            if hop.high_relevance_count == 0 and iteration > 0:
                break

            # Prepare next iteration
            iteration += 1
            if self.config.expand_entities and new_entities:
                # Search for related entities
                current_query = " ".join(list(new_entities)[:3])
            elif self.config.extract_keywords and new_keywords:
                # Expand with keywords
                current_query = query + " " + " ".join(list(new_keywords)[:3])
            else:
                # Expand time window backward
                time_start = time_start - timedelta(hours=6)
                current_query = query

        # Build causal chains from collected data
        chains = self._build_chains(
            all_incidents,
            all_events,
            initial_event,
        )

        # Find best chain
        best_chain = None
        if chains:
            best_chain = max(chains, key=lambda c: c.overall_confidence)

        end_time = datetime.utcnow()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        return MultiHopResult(
            initial_query=query,
            initial_event_id=initial_event.get("event_id") if initial_event else None,
            chains=tuple(chains),
            best_chain=best_chain,
            hops=tuple(hops),
            total_iterations=iteration,
            total_events_examined=len(all_events),
            total_incidents_examined=len(all_incidents),
            search_started=start_time,
            search_completed=end_time,
            duration_ms=duration_ms,
        )

    def _search_hop(
        self,
        iteration: int,
        query: str,
        time_start: datetime,
        time_end: datetime,
        known_entities: set[str],
    ) -> SearchHop:
        """Perform a single search hop and record results."""
        # Search incidents
        incidents = self._search_incidents(query, time_start, time_end)

        # Search events
        events = self._search_events(
            query,
            time_start,
            time_end,
            entities=list(known_entities)[:5],
        )

        # Extract entities from results
        new_entities: set[str] = set()
        for inc in incidents:
            new_entities.update(inc.incident.fingerprint.entities)
        for evt in events:
            entity = evt.get("entity")
            if entity:
                new_entities.add(entity)

        # Extract keywords
        keywords = self._extract_keywords(
            [inc.incident for inc in incidents],
            events,
        )

        # Count high-relevance results
        high_relevance = sum(1 for inc in incidents if inc.score >= self.config.high_relevance_threshold)

        return SearchHop(
            iteration=iteration,
            query=query,
            search_type="incident",
            time_window_start=time_start,
            time_window_end=time_end,
            results_count=len(incidents) + len(events),
            high_relevance_count=high_relevance,
            entities_discovered=tuple(new_entities - known_entities),
            keywords_extracted=tuple(keywords),
        )

    def _search_incidents(
        self,
        query: str,
        time_start: datetime,
        time_end: datetime,
    ) -> list[IncidentSearchResult]:
        """Search incidents matching the query."""
        try:
            results = incident_search(
                query=query,
                k=self.config.max_results_per_hop,
                search_type="hybrid",
            )
            # Filter by time window
            filtered = [r for r in results if time_start <= r.incident.created_at <= time_end]
            return filtered
        except Exception:
            return []

    def _search_events(
        self,
        query: str,
        time_start: datetime,
        time_end: datetime,
        entities: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search telemetry events matching the query."""
        try:
            from elle.daemon.telemetry.store import query_events, search_events

            # First try text search
            results = search_events(query, limit=self.config.max_results_per_hop)

            # Also search by entity if provided
            if entities:
                for entity in entities[:3]:
                    entity_results = query_events(
                        entity=entity,
                        limit=self.config.max_results_per_hop // 3,
                    )
                    for evt in entity_results:
                        if evt not in results:
                            results.append(evt)

            # Convert TelemetryEvent to dict if needed
            event_dicts = []
            for evt in results:
                if hasattr(evt, "model_dump") or hasattr(evt, "dict"):
                    event_dicts.append(safe_model_dump(evt))
                elif isinstance(evt, dict):
                    event_dicts.append(evt)

            # Filter by time window
            filtered = []
            for evt_dict in event_dicts:
                evt_time = self._get_event_time(evt_dict)
                if time_start <= evt_time <= time_end:
                    filtered.append(evt_dict)

            return filtered[: self.config.max_results_per_hop]

        except Exception:
            return []

    def _extract_keywords(
        self,
        incidents: list[IncidentReport],
        events: list[dict[str, Any]],
    ) -> set[str]:
        """Extract keywords from incidents and events."""
        keywords: set[str] = set()

        # Common technical keywords to extract
        keyword_patterns = [
            r"\b(failed|error|timeout|refused|denied|full|exhausted)\b",
            r"\b(nginx|apache|mysql|postgres|redis|docker)\b",
            r"\b(disk|memory|cpu|network|socket)\b",
            r"\b(service|process|container|mount)\b",
        ]

        texts = []
        for inc in incidents:
            texts.append(inc.title)
            texts.append(inc.summary)
            texts.extend(inc.symptoms)
        for evt in events:
            texts.append(evt.get("message", ""))

        combined = " ".join(texts).lower()

        for pattern in keyword_patterns:
            matches = re.findall(pattern, combined, re.IGNORECASE)
            keywords.update(matches)

        return keywords

    def _get_event_time(self, event: dict[str, Any]) -> datetime:
        """Get timestamp from event dict."""
        ts = event.get("ts")
        if isinstance(ts, datetime):
            return ts
        if isinstance(ts, str):
            return _parse_datetime(ts)
        return datetime.utcnow()

    def _build_chains(
        self,
        incidents: dict[str, IncidentSearchResult],
        events: dict[str, dict[str, Any]],
        initial_event: dict[str, Any] | None,
    ) -> list[CausalChain]:
        """Build causal chains from collected incidents and events.

        Uses temporal ordering and entity overlap to infer
        causal relationships.
        """
        if not incidents and not events:
            return []

        chains: list[CausalChain] = []

        # Sort all items by timestamp
        all_items: list[tuple[datetime, str, str, Any]] = []  # (time, type, id, item)

        for iid, result in incidents.items():
            all_items.append(
                (
                    result.incident.created_at,
                    "incident",
                    iid,
                    result.incident,
                )
            )

        for eid, evt in events.items():
            all_items.append(
                (
                    self._get_event_time(evt),
                    "event",
                    eid,
                    evt,
                )
            )

        all_items.sort(key=lambda x: x[0])

        if not all_items:
            return []

        # Build a simple temporal chain
        links: list[CausalLink] = []
        for i in range(len(all_items) - 1):
            curr_time, curr_type, curr_id, curr_item = all_items[i]
            next_time, next_type, next_id, next_item = all_items[i + 1]

            # Calculate time delta
            delta_sec = int((next_time - curr_time).total_seconds())

            # Skip if too far apart (> 1 hour)
            if delta_sec > 3600:
                continue

            # Determine relationship based on entity overlap and timing
            relationship = self._infer_relationship(
                curr_type,
                curr_item,
                next_type,
                next_item,
                delta_sec,
            )

            # Calculate confidence based on timing and overlap
            confidence = self._calculate_link_confidence(
                curr_type,
                curr_item,
                next_type,
                next_item,
                delta_sec,
            )

            if confidence < self.config.min_confidence:
                continue

            # Get summaries
            curr_summary = self._get_item_summary(curr_type, curr_item)
            next_summary = self._get_item_summary(next_type, next_item)

            link = CausalLink(
                cause_type=cast(CauseType, curr_type if curr_type != "incident" else "incident"),
                cause_id=curr_id,
                cause_summary=curr_summary,
                cause_timestamp=curr_time,
                effect_type=cast(EffectType, next_type if next_type != "symptom" else "symptom"),
                effect_id=next_id,
                effect_summary=next_summary,
                effect_timestamp=next_time,
                relationship=relationship,
                confidence=confidence,
                temporal_delta_sec=delta_sec,
            )
            links.append(link)

        if links:
            # Create chain
            overall_conf = 1.0
            for link in links:
                overall_conf *= link.confidence

            chain = CausalChain(
                chain_id=str(uuid.uuid4())[:8],
                root_cause=links[0] if links else None,
                links=tuple(links),
                final_symptom=links[-1].effect_summary if links else "",
                overall_confidence=overall_conf,
                search_iterations=len(incidents) + len(events),
                earliest_timestamp=all_items[0][0] if all_items else None,
                latest_timestamp=all_items[-1][0] if all_items else None,
            )
            chains.append(chain)

        return chains

    def _infer_relationship(
        self,
        cause_type: str,
        cause_item: Any,
        effect_type: str,
        effect_item: Any,
        delta_sec: int,
    ) -> Relationship:
        """Infer the type of causal relationship."""
        # Very close timing suggests direct trigger
        if delta_sec < 60:
            return "triggered"

        # Check for entity overlap
        cause_entities = self._get_entities(cause_type, cause_item)
        effect_entities = self._get_entities(effect_type, effect_item)
        overlap = cause_entities & effect_entities

        if overlap:
            if delta_sec < 300:
                return "triggered"
            return "contributed_to"

        # Default relationships based on timing
        if delta_sec < 600:
            return "preceded"
        return "correlated"

    def _calculate_link_confidence(
        self,
        cause_type: str,
        cause_item: Any,
        effect_type: str,
        effect_item: Any,
        delta_sec: int,
    ) -> float:
        """Calculate confidence score for a causal link."""
        confidence = 0.5

        # Boost for close timing
        if delta_sec < 60:
            confidence += 0.3
        elif delta_sec < 300:
            confidence += 0.2
        elif delta_sec < 600:
            confidence += 0.1

        # Boost for entity overlap
        cause_entities = self._get_entities(cause_type, cause_item)
        effect_entities = self._get_entities(effect_type, effect_item)
        overlap = cause_entities & effect_entities

        if overlap:
            confidence += 0.2 * min(1.0, len(overlap) / 2)

        # Boost for same domain
        cause_domain = self._get_domain(cause_type, cause_item)
        effect_domain = self._get_domain(effect_type, effect_item)
        if cause_domain and cause_domain == effect_domain:
            confidence += 0.1

        return min(1.0, confidence)

    def _get_entities(self, item_type: str, item: Any) -> set[str]:
        """Extract entities from an item."""
        if item_type == "incident":
            return set(item.fingerprint.entities)
        elif item_type == "event":
            entity = item.get("entity")
            return {entity} if entity else set()
        return set()

    def _get_domain(self, item_type: str, item: Any) -> str | None:
        """Get domain from an item."""
        if item_type == "incident":
            domain = item.domain
            return str(domain) if domain else None
        elif item_type == "event":
            category = item.get("category")
            return str(category) if category else None
        return None

    def _get_item_summary(self, item_type: str, item: Any) -> str:
        """Get a summary string for an item."""
        if item_type == "incident":
            title = item.title
            return str(title) if title else "Unknown"
        elif item_type == "event":
            msg = str(item.get("message", ""))
            return msg[:100] + "..." if len(msg) > 100 else msg
        return "Unknown"


# =============================================================================
# Keyword reranking utilities
# =============================================================================


def keyword_rerank(
    results: list[IncidentSearchResult],
    keywords: list[str],
    boost_factor: float = 1.5,
) -> list[IncidentSearchResult]:
    """Rerank results based on keyword density.

    Boosts scores for results that contain more of the provided keywords.

    Args:
        results: Search results to rerank.
        keywords: Keywords to look for.
        boost_factor: Maximum boost factor.

    Returns:
        Reranked results.
    """
    if not keywords:
        return results

    reranked = []
    for result in results:
        # Count keyword matches in incident text
        text = (
            result.incident.title + " " + result.incident.summary + " " + " ".join(result.incident.symptoms)
        ).lower()

        matches = sum(1 for kw in keywords if kw.lower() in text)
        keyword_boost = 1.0 + (matches / len(keywords)) * (boost_factor - 1.0)

        # Create new result with boosted score
        boosted = IncidentSearchResult(
            incident=result.incident,
            score=result.score * keyword_boost,
            match_type=result.match_type,
            precondition_match_ratio=result.precondition_match_ratio,
            outcome_weight=result.outcome_weight,
        )
        reranked.append(boosted)

    reranked.sort(key=lambda x: x.score, reverse=True)
    return reranked


def temporal_rerank(
    results: list[IncidentSearchResult],
    reference_time: datetime,
    prefer_before: bool = True,
    decay_hours: float = 12.0,
) -> list[IncidentSearchResult]:
    """Rerank results based on temporal proximity.

    Prefers results that are close to the reference time,
    optionally preferring results before (for causes) or after (for effects).

    Args:
        results: Search results to rerank.
        reference_time: Reference timestamp.
        prefer_before: If True, prefer earlier events (for finding causes).
        decay_hours: Half-life for temporal decay.

    Returns:
        Reranked results.
    """
    reranked = []
    for result in results:
        delta_hours = abs((result.incident.created_at - reference_time).total_seconds() / 3600)

        # Temporal decay
        temporal_weight = 0.5 ** (delta_hours / decay_hours)

        # Direction preference
        is_before = result.incident.created_at < reference_time
        if prefer_before and is_before or not prefer_before and not is_before:
            temporal_weight *= 1.2

        boosted = IncidentSearchResult(
            incident=result.incident,
            score=result.score * temporal_weight,
            match_type=result.match_type,
            precondition_match_ratio=result.precondition_match_ratio,
            outcome_weight=result.outcome_weight,
        )
        reranked.append(boosted)

    reranked.sort(key=lambda x: x.score, reverse=True)
    return reranked
