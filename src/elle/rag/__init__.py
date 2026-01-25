"""Agentic RAG engine for grounded reasoning.

Provides:
- LLM interface for general-purpose text and JSON generation
- Ollama client for low-level API access and embeddings
- RAG engine for grounded, verified responses (planned)

Usage:
    from elle.rag import LLM, get_llm

    llm = get_llm()
    if llm.is_available():
        response = llm.generate("Explain systemctl")
"""

# Config generation
from elle.rag.confgen import (
    ConfigGenOutcome,
    ConfigGenRequest,
    ConfigGenResult,
    ConfigGenService,
    ConfigOp,
    get_confgen_service,
)
from elle.rag.confgen import (
    generate as confgen_generate,
)
from elle.rag.llm import (
    LLM,
    LLMConfig,
    LLMError,
    LLMJSONError,
    LLMResponse,
    LLMTimeoutError,
    LLMUnavailableError,
    Message,
    get_llm,
    reset_llm,
)
from elle.rag.ollama_client import (
    OllamaClient,
    OllamaConfig,
    OllamaError,
    OllamaResponse,
    OllamaUnavailableError,
    get_client,
)

__all__ = [
    # High-level LLM interface
    "LLM",
    "LLMConfig",
    "LLMResponse",
    "LLMError",
    "LLMUnavailableError",
    "LLMTimeoutError",
    "LLMJSONError",
    "Message",
    "get_llm",
    "reset_llm",
    # Low-level Ollama client
    "OllamaClient",
    "OllamaConfig",
    "OllamaResponse",
    "OllamaError",
    "OllamaUnavailableError",
    "get_client",
    # Config generation
    "ConfigGenOutcome",
    "ConfigGenRequest",
    "ConfigGenResult",
    "ConfigGenService",
    "ConfigOp",
    "confgen_generate",
    "get_confgen_service",
]
