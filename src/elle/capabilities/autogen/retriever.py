"""Semantic capability retriever for the agentic system.

Provides multi-tier search for capabilities:
- Tier A: Exact name/keyword match (fast)
- Tier B: PostgreSQL tsvector lexical search (ts_rank_cd)
- Tier C: Semantic embedding similarity (pgvector <=>)
- Merge: Reciprocal Rank Fusion

This enables the LLM to find capabilities by natural language description
rather than exact name, e.g., "restart the web server" -> service.restart.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any, Literal

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from elle.capabilities.autogen.store import get_store
from elle.storage.engine import get_conn
from elle.storage.migrate import register_migration

if TYPE_CHECKING:
    from elle.rag.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

# PostgreSQL schema name (matches store.py)
PG_SCHEMA = "autogen"


# =============================================================================
# Models
# =============================================================================


class CapabilitySearchResult(BaseModel):
    """Result from capability search."""

    model_config = ConfigDict(frozen=True)

    capability_name: str = Field(description="Name of the matched capability")
    domain: str = Field(description="Capability domain (service, file, docker, etc.)")
    description: str = Field(description="Human-readable description")
    risk_level: str = Field(description="Risk level (none, low, medium, high, critical)")
    match_type: Literal["exact", "lexical", "semantic", "hybrid"] = Field(description="How the match was found")
    score: float = Field(ge=0.0, le=1.0, description="Combined relevance score")
    trust_weight: float = Field(ge=0.0, le=1.0, description="Trust weight from trust level")
    success_rate: float = Field(ge=0.0, le=1.0, description="Historical success rate")
    is_approved: bool = Field(description="Whether capability is approved for use")
    is_enabled: bool = Field(description="Whether capability is enabled")
    ranking_explanation: str | None = Field(default=None, description="Why this result ranked where it did")

    # For use by the LLM
    source_command: str | None = Field(default=None, description="Source command")
    summary: str | None = Field(default=None, description="Short summary for display")


# =============================================================================
# PostgreSQL FTS and Embedding Schema (via migrations)
# =============================================================================


def _migrate_to_v2(conn: psycopg.Connection) -> None:
    """Add tsvector FTS column, GIN index, and embedding/history tables."""
    # --- tsvector column + GIN index for lexical search ---
    conn.execute("""
        ALTER TABLE generated_capabilities
        ADD COLUMN IF NOT EXISTS search_tsv tsvector
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_capabilities_search_tsv
        ON generated_capabilities USING GIN (search_tsv)
    """)

    # Populate tsvector from existing rows
    conn.execute("""
        UPDATE generated_capabilities
        SET search_tsv =
            setweight(to_tsvector('english', COALESCE(capability_name, '')), 'A') ||
            setweight(to_tsvector('english', COALESCE(spec_json->>'description', '')), 'B') ||
            setweight(to_tsvector('english', COALESCE(spec_json->>'summary', '')), 'C') ||
            setweight(to_tsvector('english', COALESCE(spec_json->>'keywords', '')), 'C') ||
            setweight(to_tsvector('english', COALESCE(spec_json->>'domain', '')), 'D') ||
            setweight(to_tsvector('english', COALESCE(source_command, '')), 'D')
    """)

    # Trigger to keep tsvector in sync on INSERT
    conn.execute("""
        CREATE OR REPLACE FUNCTION capabilities_search_tsv_trigger() RETURNS trigger AS $$
        BEGIN
            NEW.search_tsv :=
                setweight(to_tsvector('english', COALESCE(NEW.capability_name, '')), 'A') ||
                setweight(to_tsvector('english', COALESCE(NEW.spec_json->>'description', '')), 'B') ||
                setweight(to_tsvector('english', COALESCE(NEW.spec_json->>'summary', '')), 'C') ||
                setweight(to_tsvector('english', COALESCE(NEW.spec_json->>'keywords', '')), 'C') ||
                setweight(to_tsvector('english', COALESCE(NEW.spec_json->>'domain', '')), 'D') ||
                setweight(to_tsvector('english', COALESCE(NEW.source_command, '')), 'D');
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
    conn.execute("""
        DROP TRIGGER IF EXISTS trg_capabilities_search_tsv
        ON generated_capabilities
    """)
    conn.execute("""
        CREATE TRIGGER trg_capabilities_search_tsv
        BEFORE INSERT OR UPDATE ON generated_capabilities
        FOR EACH ROW
        EXECUTE FUNCTION capabilities_search_tsv_trigger()
    """)

    # --- Embeddings table (pgvector) ---
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS capability_embeddings (
            capability_name TEXT PRIMARY KEY
                REFERENCES generated_capabilities(capability_name) ON DELETE CASCADE,
            embedding vector(768) NOT NULL,
            model TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
    """)

    # --- Execution history table ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS capability_execution_history (
            id SERIAL PRIMARY KEY,
            capability_name TEXT NOT NULL
                REFERENCES generated_capabilities(capability_name) ON DELETE CASCADE,
            success BOOLEAN NOT NULL,
            incident_id TEXT,
            context_query TEXT,
            executed_at TIMESTAMPTZ NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_exec_history_name
        ON capability_execution_history(capability_name)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_exec_history_success
        ON capability_execution_history(success)
    """)


register_migration(PG_SCHEMA, 2, _migrate_to_v2)


# =============================================================================
# Capability Retriever
# =============================================================================


class CapabilityRetriever:
    """Multi-tier capability retriever with semantic search.

    Search tiers:
    1. Exact name/keyword match (O(1))
    2. PostgreSQL tsvector lexical search with ts_rank_cd (O(log n))
    3. Semantic embedding similarity via pgvector (O(n))
    4. Reciprocal Rank Fusion merge

    Ranking formula:
        score = RRF_score * trust_weight * success_rate * domain_boost * precondition_factor

    Where:
        - RRF_score = sum(1/(rank + 60)) across search tiers
        - trust_weight = {core: 1.0, first_party: 0.9, third_party: 0.7}
        - success_rate = Bayesian smoothed from execution history
        - domain_boost = 1.2 if domain matches query, 1.0 otherwise
        - precondition_factor = 1.0 if deps available, 0.5 partial, 0.0 blocked
    """

    # Trust level weights
    TRUST_WEIGHTS = {
        "core": 1.0,
        "first_party": 0.9,
        "official": 0.9,
        "verified": 0.85,
        "third_party": 0.7,
    }

    # Bayesian smoothing for success rate
    PRIOR_SUCCESSES = 2  # Assume 2 successes initially
    PRIOR_TOTAL = 3  # Assume 3 total executions

    # RRF constant
    RRF_K = 60

    def __init__(self) -> None:
        """Initialize the retriever."""
        self._embedder: OllamaClient | None = None

    @property
    def embedder(self) -> OllamaClient | None:
        """Lazy-load embedder."""
        if self._embedder is None:
            try:
                from elle.rag.ollama_client import get_client

                self._embedder = get_client()
            except ImportError:
                logger.warning("Ollama client not available for embeddings")
        return self._embedder

    def search(
        self,
        query: str,
        *,
        domain: str | None = None,
        k: int = 5,
        search_type: str = "hybrid",
        include_unapproved: bool = False,
    ) -> list[CapabilitySearchResult]:
        """Search for capabilities matching a query.

        Args:
            query: Natural language query (e.g., "restart the web server").
            domain: Optional domain filter (service, file, docker, etc.).
            k: Maximum results to return.
            search_type: "exact", "lexical", "semantic", or "hybrid".
            include_unapproved: Whether to include unapproved capabilities.

        Returns:
            List of CapabilitySearchResult, ranked by relevance.
        """
        start_time = time.time()

        results_by_name: dict[str, CapabilitySearchResult] = {}
        rankings: dict[str, list[int]] = {}  # name -> [rank in each tier]

        # Tier A: Exact match
        if search_type in ("exact", "hybrid"):
            exact_results = self._search_exact(query, domain, include_unapproved)
            for i, result in enumerate(exact_results):
                results_by_name[result.capability_name] = result
                rankings.setdefault(result.capability_name, []).append(i)

        # Tier B: tsvector lexical search
        if search_type in ("lexical", "hybrid"):
            fts_results = self._search_fts(query, domain, k * 2, include_unapproved)
            for i, result in enumerate(fts_results):
                if result.capability_name not in results_by_name:
                    results_by_name[result.capability_name] = result
                rankings.setdefault(result.capability_name, []).append(i)

        # Tier C: Semantic search
        if search_type in ("semantic", "hybrid"):
            semantic_results = self._search_semantic(query, domain, k * 2, include_unapproved)
            for i, result in enumerate(semantic_results):
                if result.capability_name not in results_by_name:
                    results_by_name[result.capability_name] = result
                rankings.setdefault(result.capability_name, []).append(i)

        # Reciprocal Rank Fusion
        final_scores: dict[str, float] = {}
        for name, ranks in rankings.items():
            rrf_score = sum(1.0 / (r + self.RRF_K) for r in ranks)
            result = results_by_name[name]

            # Apply ranking factors
            final_score = (
                rrf_score * result.trust_weight * result.success_rate * self._domain_boost(query, result.domain)
            )
            final_scores[name] = final_score

        # Sort by final score
        sorted_names = sorted(final_scores.keys(), key=lambda n: final_scores[n], reverse=True)

        # Build final results
        final_results: list[CapabilitySearchResult] = []
        for name in sorted_names[:k]:
            result = results_by_name[name]
            # Update with final score
            final_results.append(
                CapabilitySearchResult(
                    capability_name=result.capability_name,
                    domain=result.domain,
                    description=result.description,
                    risk_level=result.risk_level,
                    match_type="hybrid" if len(rankings[name]) > 1 else result.match_type,
                    score=min(1.0, final_scores[name] * 10),  # Normalize
                    trust_weight=result.trust_weight,
                    success_rate=result.success_rate,
                    is_approved=result.is_approved,
                    is_enabled=result.is_enabled,
                    source_command=result.source_command,
                    summary=result.summary,
                    ranking_explanation=f"RRF from {len(rankings[name])} tier(s)",
                )
            )

        duration_ms = int((time.time() - start_time) * 1000)
        logger.debug(f"Capability search took {duration_ms}ms, found {len(final_results)} results")

        return final_results

    def _search_exact(
        self,
        query: str,
        domain: str | None,
        include_unapproved: bool,
    ) -> list[CapabilitySearchResult]:
        """Search for exact name/keyword matches."""
        results: list[CapabilitySearchResult] = []
        query_lower = query.lower()
        query_words = set(query_lower.split())

        with get_conn(schema=PG_SCHEMA) as conn:
            # Build SQL with optional filters
            sql = "SELECT * FROM generated_capabilities WHERE enabled = TRUE"
            params: list[Any] = []

            if not include_unapproved:
                sql += " AND approved = TRUE"

            cursor = conn.execute(sql, params)

            for row in cursor.fetchall():
                spec = self._parse_spec_json(row["spec_json"])
                name = row["capability_name"]
                name_lower = name.lower()

                # Check for exact match
                match_score = 0.0
                if query_lower == name_lower:
                    match_score = 1.0
                elif query_lower in name_lower:
                    match_score = 0.9
                else:
                    # Check keywords
                    keywords = spec.get("keywords", [])
                    if isinstance(keywords, str):
                        keywords = [keywords]
                    keyword_set = {k.lower() for k in keywords if k}
                    overlap = query_words & keyword_set
                    if overlap:
                        match_score = 0.8 * len(overlap) / len(query_words)

                    # Check description
                    description = spec.get("description", "").lower()
                    if query_lower in description:
                        match_score = max(match_score, 0.7)

                if match_score > 0:
                    result = self._row_to_search_result(row, spec, "exact", match_score)
                    if domain is None or result.domain == domain:
                        results.append(result)

        return sorted(results, key=lambda r: r.score, reverse=True)[:10]

    def _search_fts(
        self,
        query: str,
        domain: str | None,
        k: int,
        include_unapproved: bool,
    ) -> list[CapabilitySearchResult]:
        """Search using PostgreSQL tsvector full-text search."""
        results: list[CapabilitySearchResult] = []

        try:
            with get_conn(schema=PG_SCHEMA) as conn:
                # Build FTS query using plainto_tsquery for safe escaping
                sql = """
                    SELECT g.*,
                           ts_rank_cd(g.search_tsv, plainto_tsquery('english', %s)) AS rank
                    FROM generated_capabilities g
                    WHERE g.search_tsv @@ plainto_tsquery('english', %s)
                    AND g.enabled = TRUE
                """
                params: list[Any] = [query, query]

                if not include_unapproved:
                    sql += " AND g.approved = TRUE"

                if domain:
                    sql += " AND g.spec_json->>'domain' = %s"
                    params.append(domain)

                sql += " ORDER BY rank DESC LIMIT %s"
                params.append(k)

                cursor = conn.execute(sql, params)

                for row in cursor.fetchall():
                    spec = self._parse_spec_json(row["spec_json"])
                    # ts_rank_cd returns positive scores, higher is better
                    ts_rank = row["rank"]
                    normalized_score = max(0.0, min(1.0, float(ts_rank)))
                    results.append(self._row_to_search_result(row, spec, "lexical", normalized_score))

        except psycopg.errors.UndefinedColumn:
            logger.warning("FTS column search_tsv not found; run migrations")
        except psycopg.Error as e:
            logger.warning(f"FTS search failed: {e}")

        return results

    def _search_semantic(
        self,
        query: str,
        domain: str | None,
        k: int,
        include_unapproved: bool,
    ) -> list[CapabilitySearchResult]:
        """Search using pgvector semantic embeddings."""
        results: list[CapabilitySearchResult] = []

        # Get query embedding
        query_embedding = self._get_embedding(query)
        if query_embedding is None:
            return results

        try:
            with get_conn(schema=PG_SCHEMA) as conn:
                # pgvector cosine distance: <=> returns distance (0 = identical)
                sql = """
                    SELECT g.*, e.embedding <=> %s::vector AS distance
                    FROM capability_embeddings e
                    JOIN generated_capabilities g ON e.capability_name = g.capability_name
                    WHERE g.enabled = TRUE
                """
                params: list[Any] = [query_embedding]

                if not include_unapproved:
                    sql += " AND g.approved = TRUE"

                if domain:
                    sql += " AND g.spec_json->>'domain' = %s"
                    params.append(domain)

                sql += " ORDER BY e.embedding <=> %s::vector LIMIT %s"
                params.append(query_embedding)
                params.append(k)

                cursor = conn.execute(sql, params)

                for row in cursor.fetchall():
                    spec = self._parse_spec_json(row["spec_json"])
                    # Convert distance to similarity (1 - distance)
                    similarity = max(0.0, min(1.0, 1.0 - float(row["distance"])))
                    results.append(self._row_to_search_result(row, spec, "semantic", similarity))

        except psycopg.errors.UndefinedTable:
            logger.warning("Embedding table not found; run migrations")
        except psycopg.Error as e:
            logger.warning(f"Semantic search failed: {e}")

        return results

    def _row_to_search_result(
        self,
        row: dict[str, Any],
        spec: dict[str, Any],
        match_type: str,
        score: float,
    ) -> CapabilitySearchResult:
        """Convert a database row to a search result."""
        trust_level = row["trust_level"]
        trust_weight = self.TRUST_WEIGHTS.get(trust_level, 0.7)

        # Calculate success rate from history
        success_rate = self._get_success_rate(row["capability_name"])

        return CapabilitySearchResult(
            capability_name=row["capability_name"],
            domain=spec.get("domain", "unknown"),
            description=spec.get("description", ""),
            risk_level=spec.get("risk", "medium"),
            match_type=match_type,
            score=score,
            trust_weight=trust_weight,
            success_rate=success_rate,
            is_approved=bool(row["approved"]),
            is_enabled=bool(row["enabled"]),
            source_command=row["source_command"],
            summary=spec.get("summary", spec.get("description", "")[:100]),
        )

    def _get_success_rate(self, capability_name: str) -> float:
        """Get Bayesian-smoothed success rate for a capability."""
        try:
            with get_conn(schema=PG_SCHEMA) as conn:
                cursor = conn.execute(
                    """
                    SELECT
                        SUM(CASE WHEN success THEN 1 ELSE 0 END) AS successes,
                        COUNT(*) AS total
                    FROM capability_execution_history
                    WHERE capability_name = %s
                    """,
                    (capability_name,),
                )
                row = cursor.fetchone()

                if row and row["total"] > 0:
                    successes = row["successes"] + self.PRIOR_SUCCESSES
                    total = row["total"] + self.PRIOR_TOTAL
                    return float(successes / total)

        except psycopg.Error:
            pass

        # Default: prior success rate
        return self.PRIOR_SUCCESSES / self.PRIOR_TOTAL

    def _domain_boost(self, query: str, domain: str) -> float:
        """Calculate domain boost based on query keywords."""
        query_lower = query.lower()

        domain_keywords = {
            "service": ["service", "systemd", "restart", "start", "stop", "status"],
            "file": ["file", "read", "write", "copy", "move", "delete"],
            "docker": ["docker", "container", "image", "compose"],
            "network": ["network", "port", "firewall", "ip", "dns"],
            "package": ["package", "install", "apt", "dpkg"],
            "config": ["config", "configure", "settings"],
        }

        keywords = domain_keywords.get(domain, [])
        if any(kw in query_lower for kw in keywords):
            return 1.2

        return 1.0

    def _get_embedding(self, text: str) -> list[float] | None:
        """Get embedding for text using Ollama."""
        if self.embedder is None:
            return None

        try:
            return list(self.embedder.embed(text))  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning(f"Failed to get embedding: {e}")
            return None

    def _parse_spec_json(self, spec_json: str | dict[str, Any]) -> dict[str, Any]:
        """Parse spec JSON safely.

        PostgreSQL JSONB columns may be returned as dicts directly,
        or as strings depending on the driver configuration.
        """
        if isinstance(spec_json, dict):
            return spec_json
        try:
            return dict(json.loads(spec_json))
        except (json.JSONDecodeError, TypeError):
            return {}

    def record_execution(
        self,
        capability_name: str,
        success: bool,
        incident_id: str | None = None,
        context_query: str | None = None,
    ) -> None:
        """Record a capability execution for success rate tracking.

        Args:
            capability_name: Name of the executed capability.
            success: Whether execution succeeded.
            incident_id: Optional linked incident ID.
            context_query: Optional query that led to this execution.
        """
        try:
            from datetime import datetime, timezone

            with get_conn(schema=PG_SCHEMA) as conn:
                conn.execute(
                    """
                    INSERT INTO capability_execution_history
                    (capability_name, success, incident_id, context_query, executed_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        capability_name,
                        success,
                        incident_id,
                        context_query,
                        datetime.now(timezone.utc),
                    ),
                )

        except psycopg.Error as e:
            logger.warning(f"Failed to record execution: {e}")

    def update_embedding(self, capability_name: str) -> bool:
        """Update the embedding for a capability.

        Args:
            capability_name: Capability to update.

        Returns:
            True if updated successfully.
        """
        try:
            # Get capability spec
            store = get_store()
            stored = store.get(capability_name)
            if not stored:
                return False

            spec = self._parse_spec_json(stored.spec_json)

            # Build text for embedding
            text_parts = [
                capability_name,
                spec.get("description", ""),
                spec.get("summary", ""),
                " ".join(spec.get("keywords", [])),
                spec.get("domain", ""),
            ]
            text = " ".join(filter(None, text_parts))

            # Get embedding
            embedding = self._get_embedding(text)
            if embedding is None:
                return False

            # Store embedding -- pgvector handles list[float] natively
            from datetime import datetime, timezone

            with get_conn(schema=PG_SCHEMA) as conn:
                conn.execute(
                    """
                    INSERT INTO capability_embeddings
                    (capability_name, embedding, model, created_at)
                    VALUES (%s, %s::vector, %s, %s)
                    ON CONFLICT (capability_name) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        model = EXCLUDED.model,
                        created_at = EXCLUDED.created_at
                    """,
                    (
                        capability_name,
                        embedding,
                        "nomic-embed-text",
                        datetime.now(timezone.utc),
                    ),
                )

            return True

        except Exception as e:
            logger.warning(f"Failed to update embedding: {e}")
            return False


# =============================================================================
# Module-level retriever instance
# =============================================================================

_retriever: CapabilityRetriever | None = None


def get_retriever() -> CapabilityRetriever:
    """Get the shared retriever instance.

    Returns:
        CapabilityRetriever instance.
    """
    global _retriever
    if _retriever is None:
        _retriever = CapabilityRetriever()
    return _retriever


def reset_retriever() -> None:
    """Reset the shared retriever (for testing)."""
    global _retriever
    _retriever = None


def search_capabilities(
    query: str,
    *,
    domain: str | None = None,
    k: int = 5,
    search_type: str = "hybrid",
    include_unapproved: bool = False,
) -> list[CapabilitySearchResult]:
    """Convenience function to search capabilities.

    Args:
        query: Natural language query.
        domain: Optional domain filter.
        k: Maximum results.
        search_type: Search type.
        include_unapproved: Include unapproved capabilities.

    Returns:
        List of CapabilitySearchResult.
    """
    return get_retriever().search(
        query,
        domain=domain,
        k=k,
        search_type=search_type,
        include_unapproved=include_unapproved,
    )
