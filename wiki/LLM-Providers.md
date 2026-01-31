# LLM Providers

ELLE uses a local LLM for reasoning, planning, and text generation. By default it runs entirely through Ollama, but it also supports OpenAI-compatible remote providers.

## Default model

**`qwen2.5:7b-instruct-q8_0`**

- Q8_0 quantization for quality/memory balance
- ~8 GB VRAM (weights) + ~2 GB KV cache at 32K context
- Supports up to 128K tokens (ELLE defaults to 32K for consumer hardware)
- Balanced temperature of 0.7

## Ollama setup

### Install Ollama

```bash
curl -fsSL https://ollama.ai/install.sh | sh
```

### Pull the model

```bash
# Primary model (~8 GB)
ollama pull qwen2.5:7b-instruct-q8_0
```

### Verify

```bash
ollama list
# Should show: qwen2.5:7b-instruct-q8_0
```

### Keep-alive

ELLE configures `keep_alive=-1` which keeps the model loaded indefinitely in GPU/CPU memory. This ensures fast response times for local users. The daemon sends periodic warmup pings every 5 minutes to confirm the model stays loaded.

## Fallback chain

If the primary model fails to load, ELLE tries these models in order:

1. `qwen2.5:7b-instruct`
2. `qwen2.5:7b`
3. `llama3.1:8b-instruct-q4_0`
4. `llama3.1:8b`
5. `mistral:7b-instruct`
6. `gemma2:9b`

Pre-pull a fallback if you want automatic recovery:

```bash
ollama pull llama3.1:8b-instruct-q4_0
```

## OpenAI-compatible providers

ELLE supports any OpenAI-compatible API endpoint. This includes OpenAI, Azure OpenAI, vLLM, LM Studio, and others.

### Configuration

```toml
# elle.toml
[llm.provider]
type = "openai"
host = "https://api.openai.com/v1"
model = "gpt-4o"
api_key = ""                              # Use ELLE_LLM_API_KEY env var instead
timeout = 120.0
max_tokens = 4096
temperature = 0.7
```

### Provider examples

**OpenAI:**
```toml
[llm.provider]
type = "openai"
host = "https://api.openai.com/v1"
model = "gpt-4o"
```

**Azure OpenAI:**
```toml
[llm.provider]
type = "openai"
host = "https://your-resource.openai.azure.com/openai/deployments/your-deployment"
model = "gpt-4o"
```

**vLLM (self-hosted):**
```toml
[llm.provider]
type = "openai"
host = "http://your-vllm-server:8000/v1"
model = "meta-llama/Llama-3.1-8B-Instruct"
```

**LM Studio:**
```toml
[llm.provider]
type = "openai"
host = "http://localhost:1234/v1"
model = "local-model"
```

### API key

Set the API key via environment variable (never store in config files):

```bash
export ELLE_LLM_API_KEY="sk-..."
```

## Fallback provider

Configure a separate fallback provider (typically local Ollama) for when the primary is unavailable:

```toml
[llm.fallback]
enabled = true
host = "http://localhost:11434"
model = "qwen2.5:7b-instruct-q8_0"
retry_interval = 60.0                     # Seconds between retry attempts
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `ELLE_LLM_PROVIDER_TYPE` | `ollama` or `openai` |
| `ELLE_LLM_PROVIDER_HOST` | Provider API URL |
| `ELLE_LLM_PROVIDER_MODEL` | Model name |
| `ELLE_LLM_API_KEY` | API key for OpenAI-compatible providers |
| `ELLE_LLM_FALLBACK_ENABLED` | Enable/disable fallback (`true`/`false`) |

## Model warmup

When ELLE starts (or the daemon restarts), it sends a warmup ping to ensure the model is loaded and ready. This can take up to 5 minutes on first load (model needs to be loaded into GPU/CPU memory). After initial load, the model stays resident and subsequent requests are fast.

The warmup interval is 5 minutes — ELLE sends a minimal request (`"hi"`) to keep the model loaded. The warmup timeout is 300 seconds to accommodate initial model loading.

## Embedding model

ELLE uses a separate embedding model for semantic search in the Man Vault and incident fingerprinting:

- **Model:** `nomic-embed-text`
- **Dimension:** 768
- **Used by:** Man Vault search, incident similarity matching

This model is pulled automatically during setup. It's much smaller than the LLM and has minimal resource impact.

## Context window management

- Default context window: 32,768 tokens
- Compaction triggers at 80% (~26K tokens)
- After compaction, targets 60% (~19K tokens)
- Minimum 8 messages preserved after compaction
- Approximate token estimation: ~4 characters per token

## Low memory configurations

For systems with less than 16 GB RAM:

```bash
# Use a smaller model (~4 GB)
ollama pull qwen2.5:3b-instruct-q8_0
```

Update your config:

```toml
[llm.provider]
model = "qwen2.5:3b-instruct-q8_0"
```

Or use q4 quantization for even lower memory usage at the cost of quality.
