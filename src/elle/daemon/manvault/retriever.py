"""Man Vault retriever.

Provides search and retrieval over indexed documentation:
- Lexical search via FTS5 with BM25 ranking
- Semantic search via embeddings
- Hybrid search combining both approaches
- Snippet extraction for LLM context
- Flag verification for command validation
"""

from __future__ import annotations

import re
import sqlite3
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict

from elle.daemon.manvault.embedder import cosine_similarity, get_embedder
from elle.daemon.manvault.models import ManSnippet
from elle.daemon.manvault.schema import ensure_schema, get_connection
from elle.daemon.manvault.store import (
    get_all_embeddings,
    get_chunks_for_doc,
    get_doc,
    get_doc_by_id,
)
from elle.rag.ollama_client import OllamaError

# Preferred sections for snippet extraction (in priority order)
PREFERRED_SECTIONS = ["OPTIONS", "DESCRIPTION", "EXAMPLES", "SYNOPSIS"]

# Section priority boosts for ranking
SECTION_BOOSTS = {
    "1": 1.2,  # User commands (most common)
    "8": 1.1,  # System administration
    "5": 1.0,  # File formats
}

# Default snippet length
DEFAULT_SNIPPET_LENGTH = 2000


def search(
    query: str,
    k: int = 5,
    name: str | None = None,
    section: str | None = None,
    search_type: Literal["lexical", "semantic", "hybrid"] = "hybrid",
    conn: sqlite3.Connection | None = None,
) -> list[ManSnippet]:
    """Search the Man Vault for relevant documentation.

    Args:
        query: Search query string.
        k: Maximum number of results.
        name: Filter by command name (optional).
        section: Filter by man section (optional).
        search_type: Type of search to perform.
        conn: SQLite connection.

    Returns:
        List of matching documentation snippets.
    """
    # Get or create connection
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        if search_type == "lexical":
            return _lexical_search(query, k, name, section, conn)
        elif search_type == "semantic":
            return _semantic_search(query, k, name, section, conn)
        else:
            # Hybrid: combine both, de-duplicate, re-rank
            lexical = _lexical_search(query, k * 2, name, section, conn)
            semantic = _semantic_search(query, k * 2, name, section, conn)
            return _merge_results(lexical, semantic, k)
    finally:
        if own_conn:
            conn.close()


def _lexical_search(
    query: str,
    k: int,
    name: str | None,
    section: str | None,
    conn: sqlite3.Connection,
) -> list[ManSnippet]:
    """FTS5 search with BM25 ranking.

    Args:
        query: Search query.
        k: Maximum results.
        name: Filter by name.
        section: Filter by section.
        conn: SQLite connection.

    Returns:
        List of ManSnippet results.
    """
    cursor = conn.cursor()

    # Build FTS5 MATCH query
    # Escape special FTS5 characters
    safe_query = _escape_fts5_query(query)

    # Build WHERE clause for filters
    filters = []
    params: list[str | int] = [safe_query]

    if name:
        filters.append("d.name = ?")
        params.append(name)
    if section:
        filters.append("d.section = ?")
        params.append(section)

    filter_clause = " AND " + " AND ".join(filters) if filters else ""

    # Execute search with BM25 ranking
    cursor.execute(
        f"""
        SELECT d.id, d.name, d.section, d.text, d.priority,
               bm25(docs_fts) as score
        FROM docs_fts
        JOIN docs d ON docs_fts.rowid = d.id
        WHERE docs_fts MATCH ?{filter_clause}
        ORDER BY score
        LIMIT ?
        """,
        params + [k],
    )

    results = []
    for row in cursor:
        # Apply section boost
        boost = SECTION_BOOSTS.get(row["section"], 1.0)
        adjusted_score = abs(row["score"]) * boost + row["priority"] * 0.1

        # Extract snippet
        snippet, match_section = extract_snippet(row["text"], query)

        results.append(
            ManSnippet(
                name=row["name"],
                section=row["section"],
                snippet=snippet,
                score=adjusted_score,
                match_section=match_section,
                search_type="lexical",
            )
        )

    # Sort by adjusted score (higher is better)
    results.sort(key=lambda x: x.score, reverse=True)
    return results


def _semantic_search(
    query: str,
    k: int,
    name: str | None,
    section: str | None,
    conn: sqlite3.Connection,
) -> list[ManSnippet]:
    """Embedding-based semantic search.

    Args:
        query: Search query.
        k: Maximum results.
        name: Filter by name.
        section: Filter by section.
        conn: SQLite connection.

    Returns:
        List of ManSnippet results.
    """
    # Get embedder and generate query embedding
    embedder = get_embedder()
    if not embedder.is_available():
        return []

    try:
        query_embedding = embedder.embed_text(query)
    except OllamaError:
        return []

    # Get all embeddings (for now - will optimize for large sets)
    all_embeddings = get_all_embeddings(conn)
    if not all_embeddings:
        return []

    # Compute similarities
    similarities: list[tuple[int, float]] = []
    for chunk_id, embedding in all_embeddings:
        sim = cosine_similarity(query_embedding, embedding)
        similarities.append((chunk_id, sim))

    # Sort by similarity (highest first)
    similarities.sort(key=lambda x: x[1], reverse=True)

    # Get top chunks and their documents
    results = []
    seen_docs: set[tuple[str, str]] = set()

    cursor = conn.cursor()
    for chunk_id, sim in similarities:
        if len(results) >= k:
            break

        # Get chunk's document
        cursor.execute(
            """
            SELECT c.doc_id, c.heading, c.text,
                   d.name, d.section, d.text as doc_text
            FROM chunks c
            JOIN docs d ON c.doc_id = d.id
            WHERE c.id = ?
            """,
            (chunk_id,),
        )
        row = cursor.fetchone()
        if not row:
            continue

        # Apply filters
        if name and row["name"] != name:
            continue
        if section and row["section"] != section:
            continue

        # De-duplicate by document
        doc_key = (row["name"], row["section"])
        if doc_key in seen_docs:
            continue
        seen_docs.add(doc_key)

        # Use chunk text as snippet basis, expand with context
        snippet = _expand_chunk_snippet(row["chunk_text"] if "chunk_text" in row.keys() else row["text"], row["doc_text"])

        results.append(
            ManSnippet(
                name=row["name"],
                section=row["section"],
                snippet=snippet,
                score=sim,
                match_section=row["heading"],
                search_type="semantic",
            )
        )

    return results


def _merge_results(
    lexical: list[ManSnippet],
    semantic: list[ManSnippet],
    k: int,
) -> list[ManSnippet]:
    """Merge and re-rank results using reciprocal rank fusion.

    RRF score = sum(1 / (rank + 60)) for each list

    Args:
        lexical: Lexical search results.
        semantic: Semantic search results.
        k: Maximum results to return.

    Returns:
        Merged and re-ranked results.
    """
    # Build RRF scores
    rrf_scores: dict[tuple[str, str], float] = {}
    doc_to_snippet: dict[tuple[str, str], ManSnippet] = {}

    # Process lexical results
    for rank, snippet in enumerate(lexical):
        key = (snippet.name, snippet.section)
        rrf_scores[key] = rrf_scores.get(key, 0) + 1 / (rank + 60)
        if key not in doc_to_snippet:
            doc_to_snippet[key] = snippet

    # Process semantic results
    for rank, snippet in enumerate(semantic):
        key = (snippet.name, snippet.section)
        rrf_scores[key] = rrf_scores.get(key, 0) + 1 / (rank + 60)
        if key not in doc_to_snippet:
            doc_to_snippet[key] = ManSnippet(
                name=snippet.name,
                section=snippet.section,
                snippet=snippet.snippet,
                score=snippet.score,
                match_section=snippet.match_section,
                search_type="hybrid",
            )

    # Sort by RRF score
    sorted_keys = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)

    # Build final results
    results = []
    for key in sorted_keys[:k]:
        snippet = doc_to_snippet[key]
        results.append(
            ManSnippet(
                name=snippet.name,
                section=snippet.section,
                snippet=snippet.snippet,
                score=rrf_scores[key],
                match_section=snippet.match_section,
                search_type="hybrid",
            )
        )

    return results


def extract_snippet(
    text: str,
    query: str,
    max_length: int = DEFAULT_SNIPPET_LENGTH,
) -> tuple[str, str | None]:
    """Extract the best snippet from a document.

    Finds the most relevant portion of the document based on:
    1. Query term occurrences
    2. Preferred section priority (OPTIONS > DESCRIPTION > EXAMPLES)
    3. Sentence boundaries

    Args:
        text: Full document text.
        query: Search query for relevance scoring.
        max_length: Maximum snippet length in characters.

    Returns:
        Tuple of (snippet, section_name).
    """
    # Parse document into sections
    sections = _parse_sections(text)

    if not sections:
        # No sections found, return beginning of text
        return text[:max_length], None

    # Score sections by relevance
    query_terms = set(query.lower().split())
    section_scores: list[tuple[str, str, float]] = []

    for section_name, section_text in sections:
        # Base score from query term occurrences
        text_lower = section_text.lower()
        term_score = sum(text_lower.count(term) for term in query_terms)

        # Boost for preferred sections
        section_boost = 1.0
        if section_name:
            if section_name.upper() in PREFERRED_SECTIONS:
                section_boost = 2.0 - (PREFERRED_SECTIONS.index(section_name.upper()) * 0.3)

        final_score = term_score * section_boost
        section_scores.append((section_name or "", section_text, final_score))

    # Sort by score
    section_scores.sort(key=lambda x: x[2], reverse=True)

    # Get best section
    best_name, best_text, _ = section_scores[0]

    # If section is short enough, return it
    if len(best_text) <= max_length:
        return best_text.strip(), best_name or None

    # Extract window around best match
    snippet = _extract_window(best_text, query_terms, max_length)
    return snippet.strip(), best_name or None


def _parse_sections(text: str) -> list[tuple[str | None, str]]:
    """Parse document into sections.

    Args:
        text: Document text.

    Returns:
        List of (section_name, section_text) tuples.
    """
    # Pattern for section headings (all caps at start of line)
    pattern = re.compile(r"^([A-Z][A-Z\s]+)$", re.MULTILINE)
    matches = list(pattern.finditer(text))

    if not matches:
        return [(None, text)]

    sections = []

    # Handle preamble
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append((None, preamble))

    # Extract each section
    for i, match in enumerate(matches):
        heading = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            sections.append((heading, content))

    return sections


def _extract_window(text: str, query_terms: set[str], max_length: int) -> str:
    """Extract a window around the best query term matches.

    Args:
        text: Section text.
        query_terms: Set of query terms.
        max_length: Maximum length.

    Returns:
        Extracted snippet.
    """
    text_lower = text.lower()

    # Find all term positions
    positions: list[int] = []
    for term in query_terms:
        pos = 0
        while True:
            pos = text_lower.find(term, pos)
            if pos == -1:
                break
            positions.append(pos)
            pos += 1

    if not positions:
        # No matches, return beginning
        return _trim_to_sentence(text[:max_length])

    # Find position with most nearby matches
    best_pos = positions[0]
    best_count = 0

    for pos in positions:
        # Count matches within window
        count = sum(1 for p in positions if abs(p - pos) < max_length // 2)
        if count > best_count:
            best_count = count
            best_pos = pos

    # Extract window centered on best position
    start = max(0, best_pos - max_length // 3)
    end = min(len(text), start + max_length)

    # Adjust start to word boundary
    if start > 0:
        space_pos = text.rfind(" ", max(0, start - 50), start)
        if space_pos != -1:
            start = space_pos + 1

    snippet = text[start:end]
    return _trim_to_sentence(snippet)


def _trim_to_sentence(text: str) -> str:
    """Trim text to end at a sentence boundary.

    Args:
        text: Text to trim.

    Returns:
        Trimmed text.
    """
    # Look for sentence endings near the end
    for i in range(len(text) - 1, max(0, len(text) - 100), -1):
        if text[i] in ".!?" and (i + 1 >= len(text) or text[i + 1].isspace()):
            return text[: i + 1]

    # No sentence boundary found, return as-is
    return text


def _expand_chunk_snippet(chunk_text: str, doc_text: str, max_length: int = DEFAULT_SNIPPET_LENGTH) -> str:
    """Expand a chunk snippet with surrounding context.

    Args:
        chunk_text: The chunk text.
        doc_text: Full document text.
        max_length: Maximum snippet length.

    Returns:
        Expanded snippet.
    """
    if len(chunk_text) >= max_length:
        return chunk_text[:max_length]

    # Find chunk in document
    pos = doc_text.find(chunk_text)
    if pos == -1:
        return chunk_text

    # Expand context
    expand = (max_length - len(chunk_text)) // 2
    start = max(0, pos - expand)
    end = min(len(doc_text), pos + len(chunk_text) + expand)

    return doc_text[start:end]


def _escape_fts5_query(query: str) -> str:
    """Escape special FTS5 characters in query.

    Args:
        query: Raw query string.

    Returns:
        Escaped query safe for FTS5 MATCH.
    """
    # FTS5 special characters that need escaping
    special_chars = '"*()-:'

    # Simple approach: quote each word
    words = query.split()
    escaped_words = []

    for word in words:
        # Check if word contains special chars
        if any(c in word for c in special_chars):
            # Quote the word
            word = f'"{word}"'
        escaped_words.append(word)

    return " ".join(escaped_words)


@lru_cache(maxsize=256)
def flag_exists(cmd: str, flag: str, conn: sqlite3.Connection | None = None) -> bool:
    """Check if a flag appears in a command's man page.

    Uses caching for repeated lookups.

    Args:
        cmd: Command name (e.g., "ls").
        flag: Flag to check (e.g., "-l" or "--long").
        conn: SQLite connection.

    Returns:
        True if the flag appears in the OPTIONS section.
    """
    # Get or create connection
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        # Search common sections (1, 8)
        for section in ["1", "8", "5"]:
            doc = get_doc(conn, cmd, section)
            if doc:
                # Look for flag in OPTIONS section
                text = doc.text.lower()

                # Normalize flag for search
                flag_lower = flag.lower()

                # Check for flag at word boundary
                pattern = rf"\b{re.escape(flag_lower)}\b"
                if re.search(pattern, text):
                    return True

        return False

    finally:
        if own_conn:
            conn.close()


def get_document(
    name: str,
    section: str,
    lang: str = "en",
    conn: sqlite3.Connection | None = None,
) -> str | None:
    """Get full document text.

    Args:
        name: Command/topic name.
        section: Man section.
        lang: Language code.
        conn: SQLite connection.

    Returns:
        Full document text, or None if not found.
    """
    # Get or create connection
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
        ensure_schema(conn)

    try:
        doc = get_doc(conn, name, section, lang)
        return doc.text if doc else None
    finally:
        if own_conn:
            conn.close()
