"""Configuration constants for local LLM inference.

Centralized configuration for LLM inference via Ollama, including model names,
keep_alive settings, context window sizes, and context management thresholds.

Memory Requirements (Q8_0 quantization):
- qwen2.5:7b-instruct-q8_0: ~8GB VRAM (weights) + ~2GB KV cache at 32K context

Context Window:
- Qwen2.5-7B supports up to 128K tokens
- Default: 32K tokens (practical for consumer hardware, ample for conversations)
- Compaction triggers at 80% (~26K tokens), targets 60% (~19K tokens)

Keep Warm Strategy (for local inference):
- LLM_KEEP_ALIVE = "-1" keeps the model loaded indefinitely in Ollama
- Periodic warmup pings every 5 minutes ensure the model stays loaded
- This provides fast response times for local users
"""

from __future__ import annotations

# =============================================================================
# LLM Configuration (Large Language Model) - Generation
# =============================================================================

# Default LLM model for general text generation
# Q8_0 quantization for better memory efficiency
LLM_MODEL = "qwen2.5:7b-instruct-q8_0"

# Fallback models in order of preference
LLM_FALLBACK_MODELS = (
    "qwen2.5:7b-instruct",
    "qwen2.5:7b",
    "llama3.1:8b-instruct-q4_0",
    "llama3.1:8b",
    "mistral:7b-instruct",
    "gemma2:9b",
)

# Keep LLM loaded indefinitely for local inference
# -1 means "keep forever" in Ollama - model stays in GPU memory
# This ensures fast response times for local users
LLM_KEEP_ALIVE = "-1"

# Interval for periodic warmup pings (in seconds)
# Sends a minimal request to ensure model stays loaded
LLM_WARMUP_INTERVAL = 300  # 5 minutes

# 32K context window for multi-turn conversations
# Qwen2.5-7B supports up to 128K, but 32K is practical for consumer hardware
# while providing ample room for extended conversations
LLM_NUM_CTX = 32768

# Balanced temperature for generation
LLM_TEMPERATURE = 0.7

# Max tokens for generation responses
LLM_MAX_TOKENS = 4096

# Timeout for LLM requests (may need longer for complex generations)
LLM_TIMEOUT = 120.0


# =============================================================================
# Context Window Management
# =============================================================================

# Trigger compaction when context reaches 80% of max (~26K tokens at 32K window)
# Higher threshold since we have plenty of headroom
CONTEXT_COMPACT_THRESHOLD = 0.80

# Target 60% of max context after compaction (~19K tokens)
# Preserves more conversation history
CONTEXT_COMPACT_TARGET = 0.60

# Minimum messages to keep after compaction (system + recent turns)
CONTEXT_MIN_MESSAGES = 8

# Approximate characters per token for estimation
# This is a rough estimate; actual varies by model and content
CHARS_PER_TOKEN = 4


# =============================================================================
# Model Warmup Configuration
# =============================================================================

# Warmup ping prompt (minimal generation to load model)
WARMUP_PROMPT = "hi"

# Warmup timeout (model loading can take time on first run)
WARMUP_TIMEOUT = 300.0  # 5 minutes


# =============================================================================
# JSON Mode Configuration
# =============================================================================

# Temperature for JSON retry attempts
JSON_RETRY_TEMPERATURE = 0.1

# Maximum retries for JSON parsing
JSON_MAX_RETRIES = 1


# =============================================================================
# Embedding Model Configuration
# =============================================================================

# Default embedding model for semantic search
EMBEDDING_MODEL = "nomic-embed-text"

# Embedding dimension (for nomic-embed-text)
EMBEDDING_DIMENSION = 768
