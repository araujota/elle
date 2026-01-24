"""Man Vault Pydantic models.

Data structures for the Man Vault documentation index:
- ManDoc: Full man page document
- ManChunk: Document chunk for embedding
- ManSnippet: Search result with snippet
- ManVaultStatus: Index status report
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ManDoc(BaseModel):
    """A full document in the Man Vault.

    Represents a complete man page indexed from /usr/share/man/.
    """

    model_config = ConfigDict(frozen=True)

    id: int | None = None
    name: str  # e.g., "ls"
    section: str  # e.g., "1"
    lang: str = "en"
    source_path: str
    sha256: str  # For change detection
    text: str
    updated_at: datetime
    package_hint: str | None = None
    priority: int = 0  # Boost important commands


class ManChunk(BaseModel):
    """A chunk of a man page for embedding.

    Man pages are split into chunks (~500-1000 chars) for:
    - Efficient embedding generation
    - More precise semantic search
    - Better snippet extraction
    """

    model_config = ConfigDict(frozen=True)

    id: int | None = None
    doc_id: int
    chunk_index: int
    text: str  # ~500-1000 chars
    heading: str | None = None  # Section heading (OPTIONS, DESCRIPTION)
    start_line: int | None = None
    end_line: int | None = None


class ManSnippet(BaseModel):
    """A search result with extracted snippet.

    Returned by search operations, contains the most relevant
    portion of the man page for the query.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    section: str
    snippet: str  # ~1200-2000 chars
    score: float = Field(ge=0.0)
    match_section: str | None = None  # OPTIONS, DESCRIPTION, etc.
    search_type: Literal["lexical", "semantic", "hybrid"] = "hybrid"


class ManVaultStatus(BaseModel):
    """Index status report.

    Provides information about the current state of the Man Vault.
    """

    model_config = ConfigDict(frozen=True)

    total_docs: int
    total_chunks: int
    embedded_chunks: int
    indexed_at: datetime | None = None
    embedding_model: str | None = None
    sections: dict[str, int] = Field(default_factory=dict)  # {'1': 19443, '8': 1034}
    db_size_bytes: int
    is_indexing: bool = False
    is_embedding: bool = False


class ManDiscoveryItem(BaseModel):
    """A discovered man page file.

    Represents a man page file found during discovery,
    before it has been rendered and indexed.
    """

    model_config = ConfigDict(frozen=True)

    name: str  # e.g., "ls"
    section: str  # e.g., "1"
    lang: str = "en"
    source_path: str
    mtime: float  # File modification time
