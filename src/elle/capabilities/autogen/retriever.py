"""Semantic capability retriever for the agentic system.

Provides multi-tier search for capabilities:
- Tier A: Exact name/keyword match (fast)
- Tier B: FTS5 lexical search (BM25)
- Tier C: Semantic embedding similarity
- Merge: Reciprocal Rank Fusion

This enables the LLM to find capabilities by natural language description
rather than exact name, e.g., "restart the web server" -> service.restart.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from elle.capabilities.autogen.store import DEFAULT_DB_PATH, get_store

logger = logging.getLogger(__name__)


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
    match_type: Literal["exact", "lexical", "semantic", "hybrid"] = Field(
        description="How the match was found"
    )
    score: float = Field(ge=0.0, le=1.0, description="Combined relevance score")
    trust_weight: float = Field(ge=0.0, le=1.0, description="Trust weight from trust level")
    success_rate: float = Field(ge=0.0, le=1.0, description="Historical success rate")
    is_approved: bool = Field(description="Whether capability is approved for use")
    is_enabled: bool = Field(description="Whether capability is enabled")
    ranking_explanation: str | None = Field(
        default=None, description="Why this result ranked where it did"
    )

    # For use by the LLM
    source_command: str | None = Field(default=None, description="Source command")
    summary: str | None = Field(default=None, description="Short summary for display")


# =============================================================================
# FTS5 and Embedding Schema Extensions
# =============================================================================

FTS_SCHEMA = """
-- FTS5 virtual table for lexical search
CREATE VIRTUAL TABLE IF NOT EXISTS capabilities_fts USING fts5(
    capability_name,
    description,
    summary,
    keywords,
    domain,
    source_command,
    tokenize='porter unicode61'
);

-- Trigger to populate FTS on insert
CREATE TRIGGER IF NOT EXISTS capabilities_fts_insert
AFTER INSERT ON generated_capabilities
BEGIN
    INSERT INTO capabilities_fts(
        rowid, capability_name, description, summary, keywords, domain, source_command
    ) VALUES (
        NEW.rowid,
        NEW.capability_name,
        json_extract(NEW.spec_json, '$.description'),
        json_extract(NEW.spec_json, '$.summary'),
        json_extract(NEW.spec_json, '$.keywords'),
        json_extract(NEW.spec_json, '$.domain'),
        NEW.source_command
    );
END;

-- Trigger to update FTS on update
CREATE TRIGGER IF NOT EXISTS capabilities_fts_update
AFTER UPDATE ON generated_capabilities
BEGIN
    DELETE FROM capabilities_fts WHERE rowid = OLD.rowid;
    INSERT INTO capabilities_fts(
        rowid, capability_name, description, summary, keywords, domain, source_command
    ) VALUES (
        NEW.rowid,
        NEW.capability_name,
        json_extract(NEW.spec_json, '$.description'),
        json_extract(NEW.spec_json, '$.summary'),
        json_extract(NEW.spec_json, '$.keywords'),
        json_extract(NEW.spec_json, '$.domain'),
        NEW.source_command
    );
END;

-- Trigger to delete from FTS on delete
CREATE TRIGGER IF NOT EXISTS capabilities_fts_delete
AFTER DELETE ON generated_capabilities
BEGIN
    DELETE FROM capabilities_fts WHERE rowid = OLD.rowid;
END;
"""

EMBEDDING_SCHEMA = """
-- Embeddings for semantic search
CREATE TABLE IF NOT EXISTS capability_embeddings (
    capability_name TEXT PRIMARY KEY,
    embedding BLOB NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Execution history for success rate calculation
CREATE TABLE IF NOT EXISTS capability_execution_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capability_name TEXT NOT NULL,
    success INTEGER NOT NULL,
    incident_id TEXT,
    context_query TEXT,
    executed_at TEXT NOT NULL,
    FOREIGN KEY (capability_name) REFERENCES generated_capabilities(capability_name)
);

CREATE INDEX IF NOT EXISTS idx_exec_history_name ON capability_execution_history(capability_name);
CREATE INDEX IF NOT EXISTS idx_exec_history_success ON capability_execution_history(success);
"""


# =============================================================================
# Capability Retriever
# =============================================================================


class CapabilityRetriever:
    """Multi-tier capability retriever with semantic search.

    Search tiers:
    1. Exact name/keyword match (O(1))
    2. FTS5 lexical search with BM25 (O(log n))
    3. Semantic embedding similarity (O(n))
    4. Reciprocal Rank Fusion merge

    Ranking formula:
        score = RRF_score × trust_weight × success_rate × domain_boost × precondition_factor

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

    def __init__(self, db_path: Path | str | None = None) -> None:
        """Initialize the retriever.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._ensure_schema()
        self._embedder = None

    def _ensure_schema(self) -> None:
        """Ensure FTS and embedding schema exists."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Try to create FTS schema
                try:
                    conn.executescript(FTS_SCHEMA)
                except sqlite3.OperationalError as e:
                    # FTS table may already exist
                    if "already exists" not in str(e):
                        logger.warning(f"Failed to create FTS schema: {e}")

                # Create embedding schema
                try:
                    conn.executescript(EMBEDDING_SCHEMA)
                except sqlite3.OperationalError as e:
                    if "already exists" not in str(e):
                        logger.warning(f"Failed to create embedding schema: {e}")

                conn.commit()
        except Exception as e:
            logger.error(f"Failed to ensure retriever schema: {e}")

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @property
    def embedder(self):
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

        # Tier B: FTS5 lexical search
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
                rrf_score
                * result.trust_weight
                * result.success_rate
                * self._domain_boost(query, result.domain)
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

        with self._get_connection() as conn:
            # Build SQL with optional filters
            sql = "SELECT * FROM generated_capabilities WHERE enabled = 1"
            params: list[Any] = []

            if not include_unapproved:
                sql += " AND approved = 1"

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
        """Search using FTS5 full-text search."""
        results: list[CapabilitySearchResult] = []

        try:
            with self._get_connection() as conn:
                # Check if FTS table exists and is populated
                try:
                    cursor = conn.execute("SELECT COUNT(*) FROM capabilities_fts")
                    count = cursor.fetchone()[0]
                    if count == 0:
                        # Populate FTS from existing capabilities
                        self._populate_fts(conn)
                except sqlite3.OperationalError:
                    # FTS table doesn't exist, try to create and populate
                    self._ensure_schema()
                    self._populate_fts(conn)

                # Escape special FTS characters
                safe_query = self._escape_fts_query(query)

                # Build FTS query
                sql = """
                    SELECT g.*, bm25(capabilities_fts) AS rank
                    FROM capabilities_fts f
                    JOIN generated_capabilities g ON f.rowid = g.rowid
                    WHERE capabilities_fts MATCH ?
                    AND g.enabled = 1
                """
                params: list[Any] = [safe_query]

                if not include_unapproved:
                    sql += " AND g.approved = 1"

                if domain:
                    sql += " AND json_extract(g.spec_json, '$.domain') = ?"
                    params.append(domain)

                sql += " ORDER BY rank LIMIT ?"
                params.append(k)

                cursor = conn.execute(sql, params)

                for row in cursor.fetchall():
                    spec = self._parse_spec_json(row["spec_json"])
                    # BM25 returns negative scores, closer to 0 is better
                    bm25_score = abs(row["rank"])
                    normalized_score = max(0.0, min(1.0, 1.0 / (1.0 + bm25_score / 10)))
                    results.append(self._row_to_search_result(row, spec, "lexical", normalized_score))

        except sqlite3.OperationalError as e:
            logger.warning(f"FTS search failed: {e}")

        return results

    def _search_semantic(
        self,
        query: str,
        domain: str | None,
        k: int,
        include_unapproved: bool,
    ) -> list[CapabilitySearchResult]:
        """Search using semantic embeddings."""
        results: list[CapabilitySearchResult] = []

        # Get query embedding
        query_embedding = self._get_embedding(query)
        if query_embedding is None:
            return results

        try:
            with self._get_connection() as conn:
                # Get all capability embeddings
                sql = """
                    SELECT e.capability_name, e.embedding, g.*
                    FROM capability_embeddings e
                    JOIN generated_capabilities g ON e.capability_name = g.capability_name
                    WHERE g.enabled = 1
                """
                params: list[Any] = []

                if not include_unapproved:
                    sql += " AND g.approved = 1"

                if domain:
                    sql += " AND json_extract(g.spec_json, '$.domain') = ?"
                    params.append(domain)

                cursor = conn.execute(sql, params)

                scored_results: list[tuple[float, sqlite3.Row]] = []
                for row in cursor.fetchall():
                    cap_embedding = self._deserialize_embedding(row["embedding"])
                    if cap_embedding:
                        similarity = self._cosine_similarity(query_embedding, cap_embedding)
                        scored_results.append((similarity, row))

                # Sort by similarity
                scored_results.sort(key=lambda x: x[0], reverse=True)

                for similarity, row in scored_results[:k]:
                    spec = self._parse_spec_json(row["spec_json"])
                    results.append(self._row_to_search_result(row, spec, "semantic", similarity))

        except sqlite3.OperationalError as e:
            logger.warning(f"Semantic search failed: {e}")

        return results

    def _row_to_search_result(
        self,
        row: sqlite3.Row,
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
            match_type=match_type,  # type: ignore
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
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT
                        SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successes,
                        COUNT(*) AS total
                    FROM capability_execution_history
                    WHERE capability_name = ?
                    """,
                    (capability_name,),
                )
                row = cursor.fetchone()

                if row and row["total"] > 0:
                    successes = row["successes"] + self.PRIOR_SUCCESSES
                    total = row["total"] + self.PRIOR_TOTAL
                    return successes / total

        except sqlite3.OperationalError:
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
            return self.embedder.embed(text)
        except Exception as e:
            logger.warning(f"Failed to get embedding: {e}")
            return None

    def _serialize_embedding(self, embedding: list[float]) -> bytes:
        """Serialize embedding to bytes."""
        import struct

        return struct.pack(f"{len(embedding)}f", *embedding)

    def _deserialize_embedding(self, data: bytes) -> list[float] | None:
        """Deserialize embedding from bytes."""
        import struct

        try:
            count = len(data) // 4  # 4 bytes per float
            return list(struct.unpack(f"{count}f", data))
        except Exception:
            return None

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        import math

        if len(a) != len(b):
            return 0.0

        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)

    def _escape_fts_query(self, query: str) -> str:
        """Escape special FTS5 characters."""
        # Remove/escape special characters
        special_chars = '"*()-'
        for char in special_chars:
            query = query.replace(char, " ")
        # Join words with OR for broader matching
        words = query.split()
        if len(words) > 1:
            return " OR ".join(words)
        return query

    def _parse_spec_json(self, spec_json: str) -> dict[str, Any]:
        """Parse spec JSON safely."""
        try:
            return json.loads(spec_json)
        except json.JSONDecodeError:
            return {}

    def _populate_fts(self, conn: sqlite3.Connection) -> None:
        """Populate FTS table from existing capabilities."""
        try:
            # Clear existing FTS data
            conn.execute("DELETE FROM capabilities_fts")

            # Insert all capabilities
            cursor = conn.execute("SELECT rowid, * FROM generated_capabilities")
            for row in cursor.fetchall():
                spec = self._parse_spec_json(row["spec_json"])
                conn.execute(
                    """
                    INSERT INTO capabilities_fts(
                        rowid, capability_name, description, summary, keywords, domain, source_command
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["rowid"],
                        row["capability_name"],
                        spec.get("description", ""),
                        spec.get("summary", ""),
                        json.dumps(spec.get("keywords", [])),
                        spec.get("domain", ""),
                        row["source_command"],
                    ),
                )

            conn.commit()
            logger.info("Populated FTS table from existing capabilities")

        except sqlite3.OperationalError as e:
            logger.warning(f"Failed to populate FTS: {e}")

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
            from datetime import datetime

            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO capability_execution_history
                    (capability_name, success, incident_id, context_query, executed_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        capability_name,
                        1 if success else 0,
                        incident_id,
                        context_query,
                        datetime.utcnow().isoformat(),
                    ),
                )
                conn.commit()

        except sqlite3.OperationalError as e:
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
            store = get_store(self.db_path)
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

            # Store embedding
            from datetime import datetime

            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO capability_embeddings
                    (capability_name, embedding, model, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        capability_name,
                        self._serialize_embedding(embedding),
                        "nomic-embed-text",  # Model name
                        datetime.utcnow().isoformat(),
                    ),
                )
                conn.commit()

            return True

        except Exception as e:
            logger.warning(f"Failed to update embedding: {e}")
            return False


# =============================================================================
# Module-level retriever instance
# =============================================================================

_retriever: CapabilityRetriever | None = None


def get_retriever(db_path: Path | str | None = None) -> CapabilityRetriever:
    """Get the shared retriever instance.

    Args:
        db_path: Optional database path override.

    Returns:
        CapabilityRetriever instance.
    """
    global _retriever
    if _retriever is None or (db_path and Path(db_path) != _retriever.db_path):
        _retriever = CapabilityRetriever(db_path)
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
