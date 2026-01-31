"""Incident retrieval and similarity search.

Provides multi-tier search for finding similar past incidents:
- Tier A: Fast fingerprint matching (local)
- Tier B: Lexical search via tsvector/GIN (local)
- Tier C: Semantic similarity via pgvector (local)
- Tier D: Cloud vault query (parallel, if configured)

Results are ranked by similarity x success for optimal reuse.

Cloud Retrieval:
When ELLE Cloud is configured, queries run in parallel against both
the local vault and the cloud vault. Cloud results are merged with
local results, with local results prioritized (as they may have
machine-specific efficacy data). If cloud is unavailable, the search
continues with local-only results.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any

import psycopg
import structlog

from elle.common.pydantic_compat import safe_model_dump
from elle.daemon.incidents.models import (
    Fingerprint,
    IncidentReport,
    IncidentSearchResult,
    SystemSnapshot,
)
from elle.daemon.incidents.preconditions import evaluate_preconditions
from elle.daemon.incidents.schema import ensure_schema
from elle.daemon.incidents.store import (
    _row_to_incident,
)
from elle.storage.engine import get_conn

logger = structlog.get_logger()

# PostgreSQL schema name
PG_SCHEMA = "incidents"

# Static outcome quality weights (fallback when no efficacy data available)
STATIC_OUTCOME_WEIGHTS = {
    "improved": 1.0,
    "partial": 0.6,
    "no_change": 0.3,
    "unknown": 0.2,
    "worse": 0.0,
}

# For backwards compatibility
OUTCOME_WEIGHTS = STATIC_OUTCOME_WEIGHTS

# Severity match bonuses
SEVERITY_ORDER = ["info", "warning", "error", "critical"]

# Recency decay parameters
RECENCY_HALF_LIFE_DAYS = 30  # Score halves every 30 days


def search(
    query: str | None = None,
    domain: str | None = None,
    fingerprint: Fingerprint | None = None,
    snapshot: SystemSnapshot | None = None,
    k: int = 5,
    search_type: str = "hybrid",
    min_precondition_match: float = 0.5,
    include_cloud: bool = True,
    conn: psycopg.Connection | None = None,
) -> list[IncidentSearchResult]:
    """Search for similar incidents.

    Uses a multi-tier approach with parallel local+cloud retrieval:
    1. Fingerprint matching (fast filter, local)
    2. Lexical search (tsvector, local)
    3. Semantic search (pgvector, local)
    4. Cloud vault query (parallel, if configured)
    5. Merge and rank by similarity x outcome quality

    When cloud is configured, both local and cloud queries run in parallel.
    Local results are prioritized as they may have machine-specific efficacy data.

    Args:
        query: Text query for lexical/semantic search.
        domain: Filter by incident domain.
        fingerprint: Current system fingerprint for matching.
        snapshot: Current system snapshot for precondition evaluation.
        k: Number of results to return.
        search_type: "lexical", "semantic", "fingerprint", or "hybrid".
        min_precondition_match: Minimum precondition match ratio.
        include_cloud: Whether to query cloud vault (if configured).
        conn: psycopg connection.

    Returns:
        List of IncidentSearchResult sorted by relevance.
    """
    own_conn = conn is None
    if own_conn:
        cm = get_conn(schema=PG_SCHEMA)
        conn = cm.__enter__()
        ensure_schema(conn)
    else:
        cm = None

    # At this point conn is guaranteed to be non-None
    assert conn is not None  # nosec B101

    try:
        # Run local and cloud search in parallel
        local_candidates: list[tuple[IncidentReport, float, str]] = []
        cloud_results: list[IncidentSearchResult] = []

        # Check if cloud is available
        cloud_available = False
        if include_cloud and fingerprint:
            try:
                from elle.daemon.incidents.cloud_sync import get_cloud_client

                cloud_client = get_cloud_client()
                cloud_available = cloud_client.is_available()
            except Exception:
                pass

        # Use ThreadPoolExecutor for parallel execution
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures: dict[str, Future[Any]] = {}

            # Submit local search
            futures["local"] = executor.submit(
                _local_search,
                query=query,
                domain=domain,
                fingerprint=fingerprint,
                k=k,
                search_type=search_type,
                conn=conn,
            )

            # Submit cloud search if available
            if cloud_available and fingerprint:
                futures["cloud"] = executor.submit(
                    _cloud_search,
                    fingerprint=fingerprint,
                    domain=domain,
                    k=k,
                )

            # Collect results
            for name, future in futures.items():
                try:
                    result = future.result(timeout=10)  # 10s timeout
                    if name == "local":
                        local_candidates = result
                    elif name == "cloud":
                        cloud_results = result
                except Exception as e:
                    logger.debug(f"{name} search failed", error=str(e))

        # Merge and rank local candidates
        ranked = _merge_and_rank(
            local_candidates,
            snapshot=snapshot,
            fingerprint=fingerprint,
            min_precondition_match=min_precondition_match,
            conn=conn,
        )

        # Merge cloud results (with lower priority)
        if cloud_results:
            ranked = _merge_local_and_cloud_results(ranked, cloud_results, k)

        return ranked[:k]

    finally:
        if own_conn and cm is not None:
            cm.__exit__(None, None, None)


def _local_search(
    query: str | None,
    domain: str | None,
    fingerprint: Fingerprint | None,
    k: int,
    search_type: str,
    conn: psycopg.Connection,
) -> list[tuple[IncidentReport, float, str]]:
    """Run local search tiers (A, B, C).

    This is extracted to run in a thread pool.
    """
    candidates: list[tuple[IncidentReport, float, str]] = []

    # Tier A: Fingerprint matching
    if search_type in ("fingerprint", "hybrid") and fingerprint:
        fp_results = _fingerprint_search(fingerprint, domain, k * 4, conn)
        candidates.extend(fp_results)

    # Tier B: Lexical search
    if search_type in ("lexical", "hybrid") and query:
        lex_results = _lexical_search(query, domain, k * 4, conn)
        candidates.extend(lex_results)

    # Tier C: Semantic search
    if search_type in ("semantic", "hybrid") and query:
        sem_results = _semantic_search(query, domain, k * 4, conn)
        candidates.extend(sem_results)

    return candidates


def _cloud_search(
    fingerprint: Fingerprint,
    domain: str | None,
    k: int,
) -> list[IncidentSearchResult]:
    """Query cloud vault for similar incidents.

    This is extracted to run in a thread pool.

    Returns:
        List of IncidentSearchResult from cloud, or empty list if unavailable.
    """
    try:
        from elle.daemon.incidents.cloud_sync import query_cloud_sync

        result = query_cloud_sync(
            fingerprint=fingerprint,
            domain=domain,
            limit=k,
            min_similarity=0.3,
        )

        if result is None:
            return []

        # Convert cloud matches to IncidentSearchResult
        # Note: Cloud incidents don't have full IncidentReport data,
        # so we create lightweight proxies
        search_results = []
        for match in result.matches:
            # Create a minimal incident report from cloud data
            proxy_incident = _create_cloud_incident_proxy(match)
            search_results.append(
                IncidentSearchResult(
                    incident=proxy_incident,
                    score=match.combined_score,
                    match_type="cloud",
                    precondition_match_ratio=1.0,  # Can't evaluate remotely
                    outcome_weight=STATIC_OUTCOME_WEIGHTS.get(match.outcome, 0.2),
                    source="cloud",
                )
            )

        logger.debug(
            "Cloud search returned results",
            count=len(search_results),
            total_searched=result.total_searched,
            query_time_ms=result.query_time_ms,
        )

        return search_results

    except Exception as e:
        logger.debug("Cloud search failed", error=str(e))
        return []


def _create_cloud_incident_proxy(match: Any) -> IncidentReport:
    """Create a proxy IncidentReport from cloud match data.

    Cloud matches don't have full incident data, but we can create
    a lightweight proxy for ranking and display purposes.

    Args:
        match: A CloudIncidentMatch from cloud_sync module.
    """
    return IncidentReport(
        incident_id=f"cloud:{match.cloud_id}",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        domain=match.domain,
        severity="info",
        status="resolved",
        title=f"Cloud incident {match.cloud_id[:8]}",
        summary=f"Similar incident from {match.installation_count} installation(s)",
        outcome=match.outcome,
        confidence=match.confidence,
        fingerprint=Fingerprint(),  # Cloud fingerprint not available
    )


def _merge_local_and_cloud_results(
    local_results: list[IncidentSearchResult],
    cloud_results: list[IncidentSearchResult],
    k: int,
) -> list[IncidentSearchResult]:
    """Merge local and cloud results with local priority.

    Local results are prioritized because:
    1. They have machine-specific efficacy data
    2. They have full incident details (actions, snapshots)
    3. They may be more recent/relevant to this machine

    Cloud results fill in gaps and provide collective knowledge.
    """
    # Create a set of local incident IDs for deduplication
    local_ids = {r.incident.incident_id for r in local_results}

    # Start with local results
    merged = list(local_results)

    # Add cloud results that don't overlap
    cloud_boost = 0.9  # Slight penalty for cloud results
    for cloud_result in cloud_results:
        if cloud_result.incident.incident_id not in local_ids:
            # Apply cloud boost (slight penalty)
            boosted = IncidentSearchResult(
                incident=cloud_result.incident,
                score=cloud_result.score * cloud_boost,
                match_type=cloud_result.match_type,
                precondition_match_ratio=cloud_result.precondition_match_ratio,
                outcome_weight=cloud_result.outcome_weight,
                source="cloud",
            )
            merged.append(boosted)

    # Re-sort by score
    merged.sort(key=lambda x: x.score, reverse=True)

    return merged[:k]


def find_similar(
    incident: IncidentReport,
    k: int = 5,
    exclude_self: bool = True,
    conn: psycopg.Connection | None = None,
) -> list[IncidentSearchResult]:
    """Find incidents similar to a given incident.

    Uses the incident's fingerprint, title, and summary for matching.

    Args:
        incident: The incident to find similar ones for.
        k: Number of results to return.
        exclude_self: Exclude the incident itself from results.
        conn: psycopg connection.

    Returns:
        List of similar incidents.
    """
    # Build query from incident
    query = f"{incident.title} {incident.summary}"
    if incident.symptoms:
        query += " " + " ".join(incident.symptoms)

    results = search(
        query=query,
        domain=incident.domain,
        fingerprint=incident.fingerprint,
        k=k + (1 if exclude_self else 0),
        search_type="hybrid",
        conn=conn,
    )

    if exclude_self:
        results = [r for r in results if r.incident.incident_id != incident.incident_id]

    return results[:k]


def get_prior_art(
    query: str,
    domain: str | None = None,
    fingerprint: Fingerprint | None = None,
    snapshot: SystemSnapshot | None = None,
    k: int = 3,
    include_actions: bool = True,
    include_cloud: bool = True,
    conn: psycopg.Connection | None = None,
) -> list[dict[str, Any]]:
    """Get prior art for inclusion in LLM prompts.

    Returns summarized information about similar past incidents
    suitable for injecting into prompt context. Includes successful
    actions from prior incidents so the LLM can reference what worked.

    When cloud is configured, prior art is retrieved from both local
    and cloud vaults in parallel. Cloud results are marked with
    source="cloud" and may have limited action data.

    Args:
        query: Description of the current problem.
        domain: Incident domain.
        fingerprint: Current system fingerprint.
        snapshot: Current system snapshot.
        k: Number of prior incidents to include.
        include_actions: Whether to fetch and include actions.
        include_cloud: Whether to include cloud vault results.
        conn: psycopg connection.

    Returns:
        List of dicts with prior art summaries including:
        - incident_id, title, summary, outcome
        - outcome_weight, precondition_match, score
        - decision, root_cause, verification_steps
        - successful_actions: list of commands that worked
        - fingerprint_match: similarity metrics
        - source: "local" or "cloud"
    """
    from elle.daemon.incidents.store import get_actions

    own_conn = conn is None
    if own_conn:
        cm = get_conn(schema=PG_SCHEMA)
        conn = cm.__enter__()
        ensure_schema(conn)
    else:
        cm = None

    try:
        results = search(
            query=query,
            domain=domain,
            fingerprint=fingerprint,
            snapshot=snapshot,
            k=k,
            search_type="hybrid",
            min_precondition_match=0.4,  # Slightly lower threshold for prior art
            include_cloud=include_cloud,
            conn=conn,
        )

        prior_art = []
        for result in results:
            inc = result.incident
            is_cloud = result.source == "cloud"

            # Get successful actions from this incident (local only)
            successful_actions = []
            if include_actions and not is_cloud:
                try:
                    actions = get_actions(inc.incident_id)
                    for action in actions:
                        if action.success and action.command:
                            successful_actions.append(
                                {
                                    "command": action.command,
                                    "kind": action.kind,
                                    "exit_code": action.exit_code,
                                }
                            )
                except Exception:
                    pass  # Actions not critical

            # Build fingerprint match details for context
            fingerprint_match = {}
            if fingerprint and inc.fingerprint:
                fp = inc.fingerprint
                fingerprint_match = {
                    "disk_pressure_similar": abs(fingerprint.disk_pressure - fp.disk_pressure) < 0.2,
                    "mem_pressure_similar": abs(fingerprint.mem_pressure - fp.mem_pressure) < 0.2,
                    "entities_overlap": list(set(fingerprint.entities) & set(fp.entities)),
                }

            art = {
                "incident_id": inc.incident_id,
                "title": inc.title,
                "summary": inc.summary,
                "outcome": inc.outcome,
                "outcome_weight": result.outcome_weight,
                "precondition_match": result.precondition_match_ratio,
                "score": result.score,
                "decision": inc.decision,
                "root_cause": inc.root_cause,
                "verification_steps": list(inc.verification_steps),
                "successful_actions": successful_actions,
                "fingerprint_match": fingerprint_match,
                "trigger_command": inc.trigger_command,
                "days_ago": (datetime.utcnow() - inc.updated_at).days,
                "source": result.source,
            }
            prior_art.append(art)

        return prior_art

    finally:
        if own_conn and cm is not None:
            cm.__exit__(None, None, None)


# =============================================================================
# Tier A: Fingerprint matching
# =============================================================================


def _fingerprint_search(
    fingerprint: Fingerprint,
    domain: str | None,
    limit: int,
    conn: psycopg.Connection,
) -> list[tuple[IncidentReport, float, str]]:
    """Fast fingerprint-based filtering.

    Matches on:
    - Domain
    - Entity overlap
    - Similar resource pressure levels
    """
    cursor = conn.cursor()

    # Build query
    query = "SELECT * FROM incidents WHERE 1=1"
    params: list[Any] = []

    if domain:
        query += " AND domain = %s"
        params.append(domain)

    # Only search resolved/mitigated incidents with known outcomes
    query += " AND status IN ('resolved', 'mitigated')"
    query += " AND outcome IN ('improved', 'partial')"

    query += " ORDER BY updated_at DESC LIMIT %s"
    params.append(limit * 2)

    cursor.execute(query, params)
    rows = cursor.fetchall()

    results = []
    for row in rows:
        incident = _row_to_incident(row)
        score = _compute_fingerprint_similarity(fingerprint, incident.fingerprint)
        if score > 0.1:
            results.append((incident, score, "fingerprint"))

    # Sort by score
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:limit]


def _compute_fingerprint_similarity(
    current: Fingerprint,
    past: Fingerprint,
) -> float:
    """Compute similarity between two fingerprints."""
    score = 0.0
    weights = 0.0

    # Entity overlap (high weight)
    current_entities = set(current.entities)
    past_entities = set(past.entities)
    if current_entities or past_entities:
        if current_entities and past_entities:
            overlap = len(current_entities & past_entities)
            total = len(current_entities | past_entities)
            score += 0.4 * (overlap / total)
        weights += 0.4

    # Resource pressure similarity
    def pressure_sim(a: float, b: float) -> float:
        return 1.0 - abs(a - b)

    score += 0.15 * pressure_sim(current.disk_pressure, past.disk_pressure)
    weights += 0.15

    score += 0.15 * pressure_sim(current.mem_pressure, past.mem_pressure)
    weights += 0.15

    score += 0.1 * pressure_sim(current.cpu_pressure, min(past.cpu_pressure, 1.0))
    weights += 0.1

    # Event count similarity (presence matters more than exact count)
    def count_sim(a: int, b: int) -> float:
        if a > 0 and b > 0:
            return 1.0
        if a == 0 and b == 0:
            return 1.0
        return 0.0

    score += 0.05 * count_sim(current.oom_count_1h, past.oom_count_1h)
    weights += 0.05

    score += 0.05 * count_sim(current.service_failures_1h, past.service_failures_1h)
    weights += 0.05

    score += 0.05 * count_sim(current.docker_exited_count, past.docker_exited_count)
    weights += 0.05

    # Normalize
    return score / weights if weights > 0 else 0.0


# =============================================================================
# Tier B: Lexical search (tsvector + GIN)
# =============================================================================


def _lexical_search(
    query: str,
    domain: str | None,
    limit: int,
    conn: psycopg.Connection,
) -> list[tuple[IncidentReport, float, str]]:
    """Full-text lexical search using PostgreSQL tsvector."""
    cursor = conn.cursor()

    # plainto_tsquery handles query cleaning automatically
    if not query.strip():
        return []

    sql = """
        SELECT i.*, ts_rank_cd(i.search_tsv, plainto_tsquery('english', %s)) AS score
        FROM incidents i
        WHERE i.search_tsv @@ plainto_tsquery('english', %s)
    """
    params: list[Any] = [query, query]

    if domain:
        sql += " AND i.domain = %s"
        params.append(domain)

    sql += " ORDER BY score DESC LIMIT %s"
    params.append(limit)

    try:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    except psycopg.errors.Error:
        # FTS query error, return empty
        return []

    results = []
    for row in rows:
        incident = _row_to_incident(row)
        # ts_rank_cd returns positive scores (higher is better)
        raw_score = row["score"]
        # Normalize to 0..1 range
        score = float(raw_score) / (1.0 + float(raw_score))
        results.append((incident, score, "lexical"))

    return results


# =============================================================================
# Tier C: Semantic search (pgvector)
# =============================================================================


def _semantic_search(
    query: str,
    domain: str | None,
    limit: int,
    conn: psycopg.Connection,
) -> list[tuple[IncidentReport, float, str]]:
    """Semantic search using pgvector cosine distance."""
    try:
        from elle.rag import get_client
    except ImportError:
        return []

    # Generate query embedding
    client = get_client()
    if not client.is_available():
        return []

    try:
        query_embedding = client.generate_embedding(
            model="nomic-embed-text",
            text=query,
        )
    except Exception:
        return []

    # Use pgvector <=> operator for cosine distance ordering
    # Build the query with an optional domain filter
    if domain:
        sql = """
            SELECT i.*, (e.embedding <=> %s::vector) AS distance
            FROM incidents i
            JOIN incident_embeddings e ON i.id = e.incident_id
            WHERE i.domain = %s
            ORDER BY e.embedding <=> %s::vector
            LIMIT %s
        """
        params: list[Any] = [query_embedding, domain, query_embedding, limit]
    else:
        sql = """
            SELECT i.*, (e.embedding <=> %s::vector) AS distance
            FROM incidents i
            JOIN incident_embeddings e ON i.id = e.incident_id
            ORDER BY e.embedding <=> %s::vector
            LIMIT %s
        """
        params = [query_embedding, query_embedding, limit]

    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    except Exception:
        return []

    results = []
    for row in rows:
        incident = _row_to_incident(row)
        # <=> returns cosine distance (0 = identical, 2 = opposite)
        # Convert to similarity: 1 - distance
        distance = float(row["distance"])
        similarity = max(0.0, 1.0 - distance)
        results.append((incident, similarity, "semantic"))

    return results


# =============================================================================
# Dynamic outcome weighting
# =============================================================================


def compute_dynamic_outcome_weight(
    incident: IncidentReport,
    current_fingerprint: Fingerprint | None = None,
    conn: psycopg.Connection | None = None,
) -> float:
    """Compute outcome weight using learned efficacy data.

    Combines multiple efficacy signals to produce a dynamic weight
    that reflects what actually works on THIS machine:

    - Base outcome weight (40%): Static quality based on outcome
    - Domain efficacy (20%): Learned success rate for this domain
    - Entity efficacy (20%): Success rate for involved entities
    - Approach match (20%): Success rate for similar approaches

    Args:
        incident: The candidate incident from search.
        current_fingerprint: Current system fingerprint (for entity matching).
        conn: psycopg connection.

    Returns:
        Dynamic outcome weight between 0.0 and 1.0.
    """
    from elle.daemon.incidents.efficacy import (
        EFFICACY_WEIGHT_APPROACH,
        EFFICACY_WEIGHT_BASE_OUTCOME,
        EFFICACY_WEIGHT_DOMAIN,
        EFFICACY_WEIGHT_ENTITY,
        MIN_SAMPLES_FOR_CONFIDENCE,
        PRIOR_SUCCESS_RATE,
    )

    # Base outcome weight (static)
    base_weight = STATIC_OUTCOME_WEIGHTS.get(incident.outcome, 0.2)

    # Try to get efficacy data
    try:
        from elle.daemon.incidents.efficacy_tracker import get_efficacy_context

        # Determine entities to check
        entities = incident.fingerprint.entities
        if current_fingerprint:
            # Intersection of current and incident entities for relevance
            current_set = set(current_fingerprint.entities)
            incident_set = set(incident.fingerprint.entities)
            overlap = current_set & incident_set
            if overlap:
                entities = tuple(overlap)

        ctx = get_efficacy_context(
            domain=incident.domain,
            entities=entities,
            incident=incident,
        )

        # Compute weighted combination
        total_weight = 0.0
        weighted_sum = 0.0

        # Base outcome (always included)
        weighted_sum += EFFICACY_WEIGHT_BASE_OUTCOME * base_weight
        total_weight += EFFICACY_WEIGHT_BASE_OUTCOME

        # Domain efficacy (if available with sufficient samples)
        if ctx.domain_success_rate is not None:
            confidence = min(1.0, ctx.domain_sample_size / MIN_SAMPLES_FOR_CONFIDENCE)
            effective_rate = confidence * ctx.domain_success_rate + (1 - confidence) * PRIOR_SUCCESS_RATE
            weighted_sum += EFFICACY_WEIGHT_DOMAIN * effective_rate
            total_weight += EFFICACY_WEIGHT_DOMAIN

        # Entity efficacy (average of overlapping entities)
        if ctx.entity_success_rates:
            entity_rates = list(ctx.entity_success_rates.values())
            avg_entity_rate = sum(entity_rates) / len(entity_rates)
            weighted_sum += EFFICACY_WEIGHT_ENTITY * avg_entity_rate
            total_weight += EFFICACY_WEIGHT_ENTITY

        # Approach efficacy (if available with sufficient samples)
        if ctx.approach_success_rate is not None:
            confidence = min(1.0, ctx.approach_sample_size / MIN_SAMPLES_FOR_CONFIDENCE)
            effective_rate = confidence * ctx.approach_success_rate + (1 - confidence) * PRIOR_SUCCESS_RATE
            weighted_sum += EFFICACY_WEIGHT_APPROACH * effective_rate
            total_weight += EFFICACY_WEIGHT_APPROACH

        # Normalize
        if total_weight > 0:
            return weighted_sum / total_weight
        return base_weight

    except Exception:
        # Fall back to static weight if efficacy tracking unavailable
        return base_weight


# =============================================================================
# Merge and rank
# =============================================================================


def _compute_recency_weight(updated_at: datetime) -> float:
    """Compute recency weight using exponential decay.

    More recent incidents get higher weight.
    Weight = 0.5^(days_old / half_life)

    Args:
        updated_at: When the incident was last updated.

    Returns:
        Recency weight between 0 and 1.
    """
    now = datetime.utcnow()
    age_days = (now - updated_at).total_seconds() / 86400

    # Exponential decay with half-life
    weight = 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)

    # Minimum weight of 0.1 to not completely discount old incidents
    return float(max(weight, 0.1))


def _merge_and_rank(
    candidates: list[tuple[IncidentReport, float, str]],
    snapshot: SystemSnapshot | None = None,
    fingerprint: Fingerprint | None = None,
    min_precondition_match: float = 0.5,
    conn: psycopg.Connection | None = None,
    use_dynamic_weights: bool = True,
) -> list[IncidentSearchResult]:
    """Merge results from multiple search tiers and rank.

    Uses Reciprocal Rank Fusion (RRF) for combining scores,
    then adjusts by outcome quality, precondition match, and recency.

    When use_dynamic_weights is True (default), outcome weights are computed
    using learned efficacy data from this machine's history.

    Final score = RRF_score x outcome_weight x precondition_ratio x recency_weight
    """
    # Deduplicate and combine scores using RRF
    incident_scores: dict[str, dict[str, Any]] = {}
    k = 60  # RRF constant

    # Assign ranks within each search type
    by_type: dict[str, list[tuple[IncidentReport, float]]] = {}
    for incident, score, search_type in candidates:
        if search_type not in by_type:
            by_type[search_type] = []
        by_type[search_type].append((incident, score))

    for search_type, items in by_type.items():
        # Sort by score descending
        items.sort(key=lambda x: x[1], reverse=True)

        for rank, (incident, score) in enumerate(items):
            iid = incident.incident_id
            if iid not in incident_scores:
                incident_scores[iid] = {
                    "incident": incident,
                    "rrf_score": 0.0,
                    "match_types": set(),
                    "raw_scores": {},
                }
            incident_scores[iid]["rrf_score"] += 1.0 / (k + rank + 1)
            incident_scores[iid]["match_types"].add(search_type)
            incident_scores[iid]["raw_scores"][search_type] = score

    # Build results with additional scoring
    results = []
    for iid, data in incident_scores.items():
        incident = data["incident"]

        # Outcome weight (dynamic or static)
        if use_dynamic_weights:
            outcome_weight = compute_dynamic_outcome_weight(incident, fingerprint, conn=conn)
        else:
            outcome_weight = STATIC_OUTCOME_WEIGHTS.get(incident.outcome, 0.2)

        # Precondition match
        precond_ratio = 1.0
        if incident.preconditions and (snapshot or fingerprint):
            precond_ratio, _ = evaluate_preconditions(
                list(incident.preconditions),
                snapshot=snapshot,
                fingerprint=fingerprint,
            )

        # Skip if preconditions don't match enough
        if precond_ratio < min_precondition_match:
            continue

        # Recency weight
        recency_weight = _compute_recency_weight(incident.updated_at)

        # Final score: RRF x outcome x precondition x recency
        final_score = data["rrf_score"] * outcome_weight * precond_ratio * recency_weight

        # Determine match type
        match_types = data["match_types"]
        match_type = "hybrid" if len(match_types) > 1 else list(match_types)[0]

        results.append(
            IncidentSearchResult(
                incident=incident,
                score=final_score,
                match_type=match_type,
                precondition_match_ratio=precond_ratio,
                outcome_weight=outcome_weight,
                source="local",
            )
        )

    # Sort by final score
    results.sort(key=lambda x: x.score, reverse=True)
    return results


def generate_case_text(incident: IncidentReport) -> str:
    """Generate canonical case text for embedding.

    Creates a combined text representation of an incident
    suitable for semantic search.

    Args:
        incident: The incident to generate text for.

    Returns:
        Combined text for embedding.
    """
    parts = [
        f"Title: {incident.title}",
        f"Domain: {incident.domain}",
        f"Summary: {incident.summary}",
    ]

    if incident.symptoms:
        parts.append(f"Symptoms: {', '.join(incident.symptoms)}")

    if incident.root_cause:
        parts.append(f"Root Cause: {incident.root_cause}")

    if incident.log_snippets:
        parts.append(f"Logs: {' '.join(incident.log_snippets[:3])}")

    if incident.tags:
        parts.append(f"Tags: {', '.join(incident.tags)}")

    return "\n".join(parts)


def get_status(
    conn: psycopg.Connection | None = None,
) -> dict[str, Any]:
    """Get Incident Vault status summary.

    Args:
        conn: psycopg connection.

    Returns:
        Status dict with counts and statistics.
    """
    from elle.daemon.incidents.models import IncidentVaultStatus
    from elle.daemon.incidents.store import (
        get_action_count,
        get_incident_count,
        get_snapshot_count,
    )

    own_conn = conn is None
    if own_conn:
        cm = get_conn(schema=PG_SCHEMA)
        conn = cm.__enter__()
        ensure_schema(conn)
    else:
        cm = None

    if conn is None:
        cm = get_conn(schema=PG_SCHEMA)
        conn = cm.__enter__()
        own_conn = True

    try:
        cursor = conn.cursor()

        # Counts
        total = get_incident_count()
        actions = get_action_count()
        snapshots = get_snapshot_count()

        # Embedded count
        cursor.execute("SELECT COUNT(*) AS cnt FROM incident_embeddings")
        row = cursor.fetchone()
        embedded = row["cnt"] if row else 0

        # By status
        cursor.execute("""
            SELECT status, COUNT(*) AS cnt FROM incidents GROUP BY status
        """)
        by_status = {r["status"]: r["cnt"] for r in cursor.fetchall()}

        # By domain
        cursor.execute("""
            SELECT domain, COUNT(*) AS cnt FROM incidents GROUP BY domain
        """)
        by_domain = {r["domain"]: r["cnt"] for r in cursor.fetchall()}

        # By outcome
        cursor.execute("""
            SELECT outcome, COUNT(*) AS cnt FROM incidents GROUP BY outcome
        """)
        by_outcome = {r["outcome"]: r["cnt"] for r in cursor.fetchall()}

        # Date range
        cursor.execute("SELECT MIN(created_at) AS oldest, MAX(created_at) AS newest FROM incidents")
        row = cursor.fetchone()
        oldest = row["oldest"] if row else None
        newest = row["newest"] if row else None

        # DB size via PostgreSQL pg_database_size()
        db_size = 0
        try:
            cursor.execute("SELECT pg_database_size(current_database()) AS sz")
            size_row = cursor.fetchone()
            if size_row:
                db_size = int(size_row["sz"])
        except (psycopg.errors.Error, PermissionError, OSError):
            pass

        return safe_model_dump(
            IncidentVaultStatus(
                total_incidents=total,
                total_actions=actions,
                total_snapshots=snapshots,
                embedded_incidents=embedded,
                open_count=by_status.get("open", 0),
                mitigated_count=by_status.get("mitigated", 0),
                resolved_count=by_status.get("resolved", 0),
                by_domain=by_domain,
                by_outcome=by_outcome,
                db_size_bytes=db_size,
                oldest_incident=oldest,
                newest_incident=newest,
            )
        )

    finally:
        if own_conn and cm is not None:
            cm.__exit__(None, None, None)
