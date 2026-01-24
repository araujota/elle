"""Man Vault - Local system documentation index.

Provides indexing and retrieval of system man pages for:
- System questions (RAG context)
- Flag verification
- Command documentation

Public API:
- search(): Hybrid search over indexed documentation
- get_document(): Get full man page text
- flag_exists(): Check if a flag is valid for a command
- get_status(): Get index status
- get_service(): Get background service for daemon integration
- seed_core_commands(): Seed essential system commands
- CORE_COMMANDS: List of essential commands to seed

CLI Commands:
- `elle man <query>`: Search documentation
- `elle man status`: Show index status
- `elle man reindex`: Trigger reindexing
"""

from elle.daemon.manvault.indexer import (
    CORE_COMMANDS,
    is_core_seeded,
    seed_core_commands,
)
from elle.daemon.manvault.models import (
    ManChunk,
    ManDiscoveryItem,
    ManDoc,
    ManSnippet,
    ManVaultStatus,
)
from elle.daemon.manvault.retriever import (
    flag_exists,
    get_document,
    search,
)
from elle.daemon.manvault.service import (
    ManVaultService,
    get_service,
    get_status,
)

__all__ = [
    # Models
    "ManDoc",
    "ManChunk",
    "ManSnippet",
    "ManVaultStatus",
    "ManDiscoveryItem",
    # Search/retrieval
    "search",
    "get_document",
    "flag_exists",
    # Service
    "ManVaultService",
    "get_service",
    "get_status",
    # Seeding
    "CORE_COMMANDS",
    "seed_core_commands",
    "is_core_seeded",
]
