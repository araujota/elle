"""Incident retrieval and similarity search.

Provides multi-tier search for finding similar past incidents:
- Tier A: Fast fingerprint matching
- Tier B: Lexical search via FTS5
- Tier C: Semantic similarity via embeddings

Results are ranked by similarity × success for optimal reuse.
"""

import sqlite3
from datetime import datetime
from typing import Any

from elle.daemon.incidents.models import (
    Fingerprint,
    IncidentReport,
    IncidentSearchResult,
    SystemSnapshot,
)
from elle.daemon.incidents.preconditions import evaluate_preconditions
from elle.daemon.incidents.schema import ensure_schema, get_connection
from elle.daemon.incidents.store import (
    _parse_datetime,
    _row_to_incident,
    get_all_embeddings,
)

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
    conn: sqlite3.Connection | None = None,
) -> list[IncidentSearchResult]:
    """Search for similar incidents.

    Uses a multi-tier approach:
    1. Fingerprint matching (fast filter)
    2. Lexical search (FTS5)
    3. Semantic search (embeddings, if available)
    4. Merge and rank by similarity × outcome quality

    Args:
        query: Text query for lexical/semantic search.
        domain: Filter by incident domain.
        fingerprint: Current system fingerprint for matching.
        snapshot: Current system snapshot for precondition evaluation.
        k: Number of results to return.
        search_type: "lexical", "semantic", "fingerprint", or "hybrid".
        min_precondition_match: Minimum precondition match ratio.
        conn: SQLite connection.

    Returns:
        List of IncidentSearchResult sorted by relevance.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        candidates: list[tuple[IncidentReport, float, str]] = []

        # Tier A: Fingerprint matching
        if search_type in ("fingerprint", "hybrid") and fingerprint:
            fp_results = _fingerprint_search(
                fingerprint, domain, k * 4, conn
            )
            candidates.extend(fp_results)

        # Tier B: Lexical search
        if search_type in ("lexical", "hybrid") and query:
            lex_results = _lexical_search(query, domain, k * 4, conn)
            candidates.extend(lex_results)

        # Tier C: Semantic search
        if search_type in ("semantic", "hybrid") and query:
            sem_results = _semantic_search(query, domain, k * 4, conn)
            candidates.extend(sem_results)

        # Merge and rank
        ranked = _merge_and_rank(
            candidates,
            snapshot=snapshot,
            fingerprint=fingerprint,
            min_precondition_match=min_precondition_match,
            conn=conn,
        )

        return ranked[:k]

    finally:
        if own_conn:
            conn.close()


def find_similar(
    incident: IncidentReport,
    k: int = 5,
    exclude_self: bool = True,
    conn: sqlite3.Connection | None = None,
) -> list[IncidentSearchResult]:
    """Find incidents similar to a given incident.

    Uses the incident's fingerprint, title, and summary for matching.

    Args:
        incident: The incident to find similar ones for.
        k: Number of results to return.
        exclude_self: Exclude the incident itself from results.
        conn: SQLite connection.

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
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Get prior art for inclusion in LLM prompts.

    Returns summarized information about similar past incidents
    suitable for injecting into prompt context. Includes successful
    actions from prior incidents so the LLM can reference what worked.

    Args:
        query: Description of the current problem.
        domain: Incident domain.
        fingerprint: Current system fingerprint.
        snapshot: Current system snapshot.
        k: Number of prior incidents to include.
        include_actions: Whether to fetch and include actions.
        conn: SQLite connection.

    Returns:
        List of dicts with prior art summaries including:
        - incident_id, title, summary, outcome
        - outcome_weight, precondition_match, score
        - decision, root_cause, verification_steps
        - successful_actions: list of commands that worked
        - fingerprint_match: similarity metrics
    """
    from elle.daemon.incidents.store import get_actions

    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        results = search(
            query=query,
            domain=domain,
            fingerprint=fingerprint,
            snapshot=snapshot,
            k=k,
            search_type="hybrid",
            min_precondition_match=0.4,  # Slightly lower threshold for prior art
            conn=conn,
        )

        prior_art = []
        for result in results:
            inc = result.incident

            # Get successful actions from this incident
            successful_actions = []
            if include_actions:
                try:
                    actions = get_actions(inc.incident_id, conn=conn)
                    for action in actions:
                        if action.success and action.command:
                            successful_actions.append({
                                "command": action.command,
                                "kind": action.kind,
                                "exit_code": action.exit_code,
                            })
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
            }
            prior_art.append(art)

        return prior_art

    finally:
        if own_conn:
            conn.close()


# =============================================================================
# Tier A: Fingerprint matching
# =============================================================================


def _fingerprint_search(
    fingerprint: Fingerprint,
    domain: str | None,
    limit: int,
    conn: sqlite3.Connection,
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
        query += " AND domain = ?"
        params.append(domain)

    # Only search resolved/mitigated incidents with known outcomes
    query += " AND status IN ('resolved', 'mitigated')"
    query += " AND outcome IN ('improved', 'partial')"

    query += " ORDER BY updated_at DESC LIMIT ?"
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
# Tier B: Lexical search (FTS5)
# =============================================================================


def _lexical_search(
    query: str,
    domain: str | None,
    limit: int,
    conn: sqlite3.Connection,
) -> list[tuple[IncidentReport, float, str]]:
    """FTS5 lexical search over incident text."""
    cursor = conn.cursor()

    # Build FTS query
    # Clean query for FTS5
    fts_query = _clean_fts_query(query)
    if not fts_query:
        return []

    sql = """
        SELECT i.*, bm25(incidents_fts) as score
        FROM incidents i
        JOIN incidents_fts ON i.rowid = incidents_fts.rowid
        WHERE incidents_fts MATCH ?
    """
    params: list[Any] = [fts_query]

    if domain:
        sql += " AND i.domain = ?"
        params.append(domain)

    sql += " ORDER BY score LIMIT ?"
    params.append(limit)

    try:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        # FTS query error, return empty
        return []

    results = []
    for row in rows:
        incident = _row_to_incident(row)
        # BM25 scores are negative (lower is better), normalize
        raw_score = row["score"]
        score = 1.0 / (1.0 + abs(raw_score))
        results.append((incident, score, "lexical"))

    return results


def _clean_fts_query(query: str) -> str:
    """Clean a query string for FTS5."""
    # Remove special FTS operators
    query = query.replace('"', " ")
    query = query.replace("*", " ")
    query = query.replace("-", " ")
    query = query.replace("OR", " ")
    query = query.replace("AND", " ")
    query = query.replace("NOT", " ")

    # Split into words and filter
    words = [w.strip() for w in query.split() if len(w.strip()) >= 2]

    # Rejoin with OR for broader matching
    return " OR ".join(words)


# =============================================================================
# Tier C: Semantic search
# =============================================================================


def _semantic_search(
    query: str,
    domain: str | None,
    limit: int,
    conn: sqlite3.Connection,
) -> list[tuple[IncidentReport, float, str]]:
    """Semantic search using embeddings."""
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

    # Get all incident embeddings
    embeddings = get_all_embeddings(conn)
    if not embeddings:
        return []

    # Compute similarities
    similarities = []
    for incident_id, embedding in embeddings.items():
        sim = _cosine_similarity(query_embedding, embedding)
        similarities.append((incident_id, sim))

    # Sort by similarity
    similarities.sort(key=lambda x: x[1], reverse=True)

    # Fetch top incidents
    results = []
    cursor = conn.cursor()
    for incident_id, sim in similarities[:limit]:
        if domain:
            cursor.execute(
                "SELECT * FROM incidents WHERE id = ? AND domain = ?",
                (incident_id, domain),
            )
        else:
            cursor.execute(
                "SELECT * FROM incidents WHERE id = ?",
                (incident_id,),
            )
        row = cursor.fetchone()
        if row:
            incident = _row_to_incident(row)
            results.append((incident, sim, "semantic"))

    return results


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


# =============================================================================
# Dynamic outcome weighting
# =============================================================================


def compute_dynamic_outcome_weight(
    incident: IncidentReport,
    current_fingerprint: Fingerprint | None = None,
    conn: sqlite3.Connection | None = None,
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
        conn: SQLite connection.

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
            conn=conn,
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
            effective_rate = (
                confidence * ctx.domain_success_rate
                + (1 - confidence) * PRIOR_SUCCESS_RATE
            )
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
            effective_rate = (
                confidence * ctx.approach_success_rate
                + (1 - confidence) * PRIOR_SUCCESS_RATE
            )
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
    return max(weight, 0.1)


def _merge_and_rank(
    candidates: list[tuple[IncidentReport, float, str]],
    snapshot: SystemSnapshot | None = None,
    fingerprint: Fingerprint | None = None,
    min_precondition_match: float = 0.5,
    conn: sqlite3.Connection | None = None,
    use_dynamic_weights: bool = True,
) -> list[IncidentSearchResult]:
    """Merge results from multiple search tiers and rank.

    Uses Reciprocal Rank Fusion (RRF) for combining scores,
    then adjusts by outcome quality, precondition match, and recency.

    When use_dynamic_weights is True (default), outcome weights are computed
    using learned efficacy data from this machine's history.

    Final score = RRF_score × outcome_weight × precondition_ratio × recency_weight
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
            outcome_weight = compute_dynamic_outcome_weight(
                incident, fingerprint, conn=conn
            )
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

        # Final score: RRF × outcome × precondition × recency
        final_score = (
            data["rrf_score"]
            * outcome_weight
            * precond_ratio
            * recency_weight
        )

        # Determine match type
        match_types = data["match_types"]
        if len(match_types) > 1:
            match_type = "hybrid"
        else:
            match_type = list(match_types)[0]

        results.append(IncidentSearchResult(
            incident=incident,
            score=final_score,
            match_type=match_type,  # type: ignore
            precondition_match_ratio=precond_ratio,
            outcome_weight=outcome_weight,
        ))

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
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Get Incident Vault status summary.

    Args:
        conn: SQLite connection.

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
        conn = get_connection()
        ensure_schema(conn)

    try:
        cursor = conn.cursor()

        # Counts
        total = get_incident_count(conn)
        actions = get_action_count(conn)
        snapshots = get_snapshot_count(conn)

        # Embedded count
        cursor.execute("SELECT COUNT(*) FROM incident_embeddings")
        embedded = cursor.fetchone()[0]

        # By status
        cursor.execute("""
            SELECT status, COUNT(*) FROM incidents GROUP BY status
        """)
        by_status = {row[0]: row[1] for row in cursor.fetchall()}

        # By domain
        cursor.execute("""
            SELECT domain, COUNT(*) FROM incidents GROUP BY domain
        """)
        by_domain = {row[0]: row[1] for row in cursor.fetchall()}

        # By outcome
        cursor.execute("""
            SELECT outcome, COUNT(*) FROM incidents GROUP BY outcome
        """)
        by_outcome = {row[0]: row[1] for row in cursor.fetchall()}

        # Date range
        cursor.execute("SELECT MIN(created_at), MAX(created_at) FROM incidents")
        row = cursor.fetchone()
        oldest = _parse_datetime(row[0]) if row[0] else None
        newest = _parse_datetime(row[1]) if row[1] else None

        # DB size
        from elle.daemon.incidents.schema import get_db_path
        db_path = get_db_path()
        db_size = db_path.stat().st_size if db_path.exists() else 0

        return IncidentVaultStatus(
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
        ).model_dump()

    finally:
        if own_conn:
            conn.close()
