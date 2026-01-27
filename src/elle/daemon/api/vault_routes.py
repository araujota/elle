"""Vault API routes for elled.

Provides REST endpoints for Man Vault and Incident Vault operations,
enabling CLI to query vaults through the daemon rather than directly.

Implements:
- POST /v1/manvault/reindex - Trigger Man Vault reindex
- GET /v1/manvault/search - Search Man Vault
- GET /v1/manvault/status - Get Man Vault status
- GET /v1/incidents/search - Search Incident Vault for similar issues
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from elle.daemon.api.auth import AuthContext, get_auth_context

if TYPE_CHECKING:
    from elle.daemon.manvault.service import ManVaultService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["vaults"])

# Service references set during app creation
_manvault_service: ManVaultService | None = None


def set_manvault_service(service: ManVaultService) -> None:
    """Set the Man Vault service reference.

    Args:
        service: The ManVaultService instance.
    """
    global _manvault_service
    _manvault_service = service


def get_manvault_service() -> ManVaultService:
    """Get the Man Vault service.

    Raises:
        HTTPException: If service not set.

    Returns:
        The ManVaultService instance.
    """
    if _manvault_service is None:
        raise HTTPException(status_code=503, detail="Man Vault service not initialized")
    return _manvault_service


# ============================================================================
# Response Models
# ============================================================================


class ManVaultStatusResponse(BaseModel):
    """Man Vault status response."""

    model_config = ConfigDict(frozen=True)

    total_docs: int = Field(description="Total indexed documents")
    total_chunks: int = Field(description="Total chunks")
    embedded_chunks: int = Field(description="Chunks with embeddings")
    indexed_at: datetime | None = Field(default=None, description="Last index time")
    embedding_model: str | None = Field(default=None, description="Embedding model")
    db_size_bytes: int = Field(default=0, description="Database size")
    is_indexing: bool = Field(default=False, description="Currently indexing")
    is_embedding: bool = Field(default=False, description="Currently embedding")
    sections: dict[str, int] = Field(default_factory=dict, description="Docs per section")


class ManVaultSearchResult(BaseModel):
    """Single search result from Man Vault."""

    model_config = ConfigDict(frozen=True)

    command: str = Field(description="Command name")
    section: str = Field(description="Man page section")
    snippet: str = Field(description="Relevant text snippet")
    score: float = Field(description="Relevance score")


class ManVaultSearchResponse(BaseModel):
    """Man Vault search response."""

    model_config = ConfigDict(frozen=True)

    results: list[ManVaultSearchResult] = Field(default_factory=list)
    total: int = Field(default=0)
    query: str = Field(description="Original query")


class ReindexResponse(BaseModel):
    """Reindex trigger response."""

    model_config = ConfigDict(frozen=True)

    status: str = Field(description="started or already_running")
    message: str = Field(description="Human-readable message")


class IncidentSearchResult(BaseModel):
    """Single search result from Incident Vault."""

    model_config = ConfigDict(frozen=True)

    incident_id: str = Field(description="Incident ID")
    title: str = Field(description="Incident title")
    domain: str = Field(description="Incident domain")
    severity: str = Field(description="Severity level")
    summary: str | None = Field(default=None, description="Incident summary")
    root_cause: str | None = Field(default=None, description="Root cause if identified")
    outcome: str | None = Field(default=None, description="Outcome status")
    score: float = Field(default=0.0, description="Relevance score")
    created_at: datetime = Field(description="Incident creation time")


class IncidentSearchResponse(BaseModel):
    """Incident Vault search response."""

    model_config = ConfigDict(frozen=True)

    results: list[IncidentSearchResult] = Field(default_factory=list)
    total: int = Field(default=0)
    query: str = Field(description="Original query")


# ============================================================================
# Man Vault Routes
# ============================================================================


@router.get(
    "/manvault/status",
    response_model=ManVaultStatusResponse,
    responses={401: {"description": "Authentication required"}},
)
async def get_manvault_status(
    auth: AuthContext = Depends(get_auth_context),
) -> ManVaultStatusResponse:
    """Get Man Vault indexing status. Requires authentication."""
    _ = auth
    service = get_manvault_service()
    status = service.get_status()

    return ManVaultStatusResponse(
        total_docs=status.total_docs,
        total_chunks=status.total_chunks,
        embedded_chunks=status.embedded_chunks,
        indexed_at=status.indexed_at,
        embedding_model=status.embedding_model,
        db_size_bytes=status.db_size_bytes,
        is_indexing=status.is_indexing,
        is_embedding=status.is_embedding,
        sections=status.sections,
    )


@router.post(
    "/manvault/reindex",
    response_model=ReindexResponse,
    responses={401: {"description": "Authentication required"}},
)
async def trigger_manvault_reindex(
    auth: AuthContext = Depends(get_auth_context),
) -> ReindexResponse:
    """Trigger a full Man Vault reindex. Requires authentication.

    The reindex runs asynchronously in the background.
    Use /manvault/status to check progress.
    """
    _ = auth
    service = get_manvault_service()

    if service.is_indexing:
        return ReindexResponse(
            status="already_running",
            message="Indexing is already in progress",
        )

    # Trigger async reindex
    await service.trigger_reindex()

    return ReindexResponse(
        status="started",
        message="Reindexing started in background. Use /manvault/status to check progress.",
    )


@router.get(
    "/manvault/search",
    response_model=ManVaultSearchResponse,
    responses={401: {"description": "Authentication required"}},
)
async def search_manvault(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=50, description="Max results"),
    use_semantic: bool = Query(True, description="Enable semantic search"),
    auth: AuthContext = Depends(get_auth_context),
) -> ManVaultSearchResponse:
    """Search the Man Vault for relevant documentation. Requires authentication.

    Supports hybrid search combining BM25 lexical matching
    and semantic similarity (when embeddings are available).
    """
    _ = auth
    try:
        from elle.daemon.manvault import search

        # Perform search
        # The search function uses `k` for limit and `search_type` for semantic mode
        search_type: Literal["lexical", "semantic", "hybrid"] = "hybrid" if use_semantic else "lexical"
        results = search(
            query=q,
            k=limit,
            search_type=search_type,
        )

        return ManVaultSearchResponse(
            results=[
                ManVaultSearchResult(
                    command=r.name,  # ManSnippet uses 'name' not 'command'
                    section=r.section,
                    snippet=r.snippet[:500],  # Truncate long snippets
                    score=r.score,
                )
                for r in results
            ],
            total=len(results),
            query=q,
        )

    except Exception as e:
        logger.error(f"Man Vault search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# ============================================================================
# Incident Vault Routes
# ============================================================================


@router.get(
    "/incidents/search",
    response_model=IncidentSearchResponse,
    responses={401: {"description": "Authentication required"}},
)
async def search_incidents(
    q: str = Query(..., min_length=1, description="Search query (error message or description)"),
    limit: int = Query(10, ge=1, le=50, description="Max results"),
    domain: str | None = Query(None, description="Filter by domain"),
    use_semantic: bool = Query(True, description="Enable semantic search"),
    auth: AuthContext = Depends(get_auth_context),
) -> IncidentSearchResponse:
    """Search the Incident Vault for similar past incidents. Requires authentication.

    Used by Fixit to find prior art for error diagnosis.
    Supports hybrid search with semantic similarity.
    """
    _ = auth
    try:
        from elle.daemon.incidents.retriever import search as do_search

        # Perform search
        results = do_search(
            query=q,
            k=limit,
            domain=domain,
            search_type="hybrid" if use_semantic else "lexical",
        )

        return IncidentSearchResponse(
            results=[
                IncidentSearchResult(
                    incident_id=r.incident.incident_id,
                    title=r.incident.title,
                    domain=r.incident.domain,
                    severity=r.incident.severity,
                    summary=r.incident.summary,
                    root_cause=r.incident.root_cause,
                    outcome=r.incident.outcome,
                    score=r.score,
                    created_at=r.incident.created_at,
                )
                for r in results
            ],
            total=len(results),
            query=q,
        )

    except ImportError:
        # search_incidents may not exist yet - return empty
        return IncidentSearchResponse(
            results=[],
            total=0,
            query=q,
        )
    except Exception as e:
        logger.error(f"Incident search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
