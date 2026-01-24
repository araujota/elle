# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ELLE (Enabling Layer Learning Everything) is a local-first, agentic system layer for Ubuntu 24.04 LTS that converts kernel-level telemetry into natural language insight and safe system operations. Distributed as a `.deb` package via PPA.

## Architecture

Four primary components:

1. **elled** (daemon) - Background service handling telemetry ingestion, event storage, Man Vault indexing, incident tracking, and privileged operations via Polkit helper
2. **elle** (CLI) - User-facing interactive terminal REPL and one-shot commands
3. **Man Vault** - Local SQLite+FTS5 index of system documentation (`/usr/share/man/**`)
4. **Incident Vault** - Decision memory storing incident reports, actions, and outcomes for learning

```
User ──▶ elle (terminal / CLI)
         ├─▶ Man Vault (local documentation grounding)
         ├─▶ Incident Vault (decision memory + prior art)
         ├─▶ Ollama (local inference)
         └─▶ elled (telemetry + privileged ops)
```

## Repository Structure

```
src/elle/
  cli/
    engine.py            # Core command processing (shared by REPL and one-shot)
    oneshot.py           # One-shot command entry point
    subprocess_runner.py # Safe subprocess execution with denylist
    terminal/
      repl.py            # Interactive REPL loop
      classifier.py      # Intent classification
      executor.py        # Command execution
      renderer.py        # Output formatting (prompts, colors, plans)
    fixit/               # Command failure recovery
    planner/             # System task planning
  daemon/
    telemetry/           # Journal/kernel watchers, probes
    manvault/            # Man page documentation indexing
    incidents/           # Incident report vault (decision memory)
    ops/                 # Privileged operations
    api/                 # FastAPI bridge (optional)
  ops/
    files/
      models.py          # FileOp, ReadResult, WriteResult
      handler.py         # FileHandler with read/write/move/copy/delete
      text.py            # TextHandler, MarkdownBuilder
      organizer.py       # Smart file organization
    augeas/              # Augeas-based config editing
  rag/
    llm.py               # High-level LLM interface
    ollama_client.py     # Low-level Ollama HTTP client
    confgen/
      models.py          # ConfigGenRequest, ConfigOp, ConfigGenResult
      prompts.py         # System prompts, templates
      domains.py         # Domain handlers (netplan, ssh, fstab, etc.)
      generator.py       # LLMConfigGenerator
      validator.py       # Pre-apply validation
      service.py         # ConfigGenService
  security/              # Polkit integration
  common/
    session.py           # Immutable session state
    models.py            # Pydantic models (TelemetryEvent, CommandPlan, etc.)
    db.py                # SQLite utilities
packaging/               # Debian packaging files
tests/                   # pytest test suite
```

## Engine Architecture

All input flows through a shared `Engine` class, enabling consistent behavior between REPL and one-shot modes:

```
┌─────────────────┐     ┌─────────────────┐
│   REPL (elle)   │     │  One-shot CLI   │
└────────┬────────┘     └────────┬────────┘
         │                       │
         └───────────┬───────────┘
                     │
              ┌──────▼──────┐
              │   Engine    │
              │  .process() │
              └──────┬──────┘
                     │
    ┌────────────────┼────────────────┐
    │                │                │
┌───▼────┐    ┌──────▼──────┐   ┌─────▼─────┐
│Classify│    │   Execute   │   │  Render   │
└────────┘    └─────────────┘   └───────────┘
```

**Key design decisions:**
- `Session` is immutable (frozen dataclass) - operations return new instances
- `Engine.process()` takes input + session, returns `EngineResult` + new session
- `EngineResult` includes output, updated session, action signal, and success flag
- `EngineAction` enum signals caller behavior (CONTINUE, EXIT, CLEAR)

## Development Commands

```bash
# Create virtual environment (first time)
python3 -m venv .venv
source .venv/bin/activate

# Install in development mode
pip install -e ".[dev]"

# Run the REPL
elle

# Run one-shot command
elle help
elle status

# Run tests
pytest

# Run single test file
pytest tests/test_engine.py

# Run tests with coverage
pytest --cov=elle

# Lint
ruff check src/

# Format
ruff format src/

# Type check
mypy src/
```

## Core Design Principles

- **Local-first**: All inference runs locally via Ollama
- **No ambient sudo**: Privileged actions are discrete, auditable, Polkit-gated
- **Intent before execution**: Every user input classified before action
- **Explain → Plan → Confirm → Apply**: Never silently mutate the system
- **Pydantic everywhere**: Use Pydantic models for all data objects, inputs, outputs, and API boundaries to ensure robust type validation at runtime

## Intent Classification

Every input must be classified into exactly one intent before execution:

| Intent | Description |
|--------|-------------|
| `shell_passthrough` | Safe shell command (`ls`, `cat file`) |
| `system_question` | Explanation or diagnosis request |
| `system_task` | Requested system change |
| `fixit` | Repair a failed command |
| `navigation` | `status`, `events`, `logs` |
| `meta` | `help`, `exit`, `config` |

## Key Data Models

```python
class TelemetryEvent(BaseModel):
    ts: datetime
    source: Literal["journal", "kernel", "probe"]
    severity: Literal["info", "warning", "error", "critical"]
    category: str
    message: str
    raw: dict

class CommandPlan(BaseModel):
    explanation: str
    commands: list[str]
    checks: list[str]
    rollback: list[str]
    risks: list[str]
    requires_privilege: bool
```

## Storage Locations

- Event database: `/var/lib/elle/elle.db` (SQLite - telemetry events)
- Man Vault: `/var/lib/elle/manvault.db` (SQLite+FTS5 - documentation index)
- Incident Vault: `/var/lib/elle/incidents.db` (SQLite+FTS5 - incident reports)
- Config backups: `/var/lib/elle/backups/<domain>/<timestamp>/`

## Build Order

### Alpha
1. ELLE terminal REPL
2. Intent classifier
3. Shell passthrough + fixit
4. Man Vault indexing
5. Local RAG + verification

### Beta
6. elled daemon
7. Telemetry DB
8. Downloads automation
9. ntfy alerts

### Release
10. Debian packaging
11. PPA
12. Mobile bridge
13. eBPF hooks (optional)

## Telemetry Sources

- **Journal Watcher**: `journalctl -f -o json` (OOM kills, auth events, network drops, service crashes)
- **Kernel Watcher**: `journalctl -k` (disk I/O errors, thermal throttling, NIC link changes)
- **Periodic Probes**: `smartctl`, `sensors`, `df -h`, `ip link`

## Security Model

- Daemon runs unprivileged
- Minimal Polkit helper for atomic operations (edit netplan, ufw, fstab, cron, read privileged logs)
- All config edits: backup → apply via Augeas/temp file → validate → commit/rollback

## Intent Classifier Implementation

The intent classifier (`cli/terminal/classifier.py`) uses a hybrid approach:

**Classification precedence:**
1. **Hard keyword routes** - Exact matches for meta/navigation/fixit triggers (no SLM needed)
2. **Prefix commands** - `/ask`, `/do`, `/sh`, `/fix`, `!` for explicit intent
3. **Pattern matching** - Regex patterns for shell commands, questions, task requests
4. **SLM classification** - Ollama fallback for ambiguous inputs
5. **Safety overrides** - Reduce confidence and flag dangerous commands

**Key files:**
- `cli/terminal/intent.py` - Intent enum and IntentResult Pydantic model
- `cli/terminal/classifier.py` - IntentClassifier with hybrid routing
- `rag/ollama_client.py` - Low-level Ollama HTTP client (classification, embeddings)
- `rag/llm.py` - High-level LLM interface for general use

**Confidence thresholds:**
- `HIGH_CONFIDENCE = 0.90` - Rule-based exact matches
- `MEDIUM_CONFIDENCE = 0.75` - Partial matches
- `CONFIDENCE_THRESHOLD = 0.55` - Below this, require clarification

**Safety overrides:**
Dangerous commands (from denylist) have confidence reduced to 30% of original value and `requires_clarification=True`.

**Classification logging:**
All classifications logged to `~/.local/state/elle/intent_log.jsonl` for model tuning.

**Golden test set:**
`tests/intent_cases.jsonl` contains ~200 test cases covering all intent categories.

## Subprocess Runner & Denylist

All shell commands go through `subprocess_runner.py` which provides:
- **Timeout protection** (default 30s)
- **Streaming or capture modes**
- **Exit code, stdout, stderr capture** for `fix` command analysis
- **No implicit sudo** - sudo commands are blocked

**Denylist categories** (commands blocked even in passthrough):
| Category | Examples |
|----------|----------|
| `DESTRUCTIVE_RM` | `rm -rf /`, `rm -rf /etc` |
| `FILESYSTEM_FORMAT` | `mkfs`, `mkswap` |
| `RAW_DISK_WRITE` | `dd of=/dev/sda` |
| `FORK_BOMB` | `:(){:\|:&};:` |
| `RECURSIVE_PERMISSION` | `chmod -R 777 /`, `chown -R` on system dirs |
| `PIPE_TO_SHELL` | `curl ... \| bash`, `wget ... \| sh` |
| `SUDO_ATTEMPT` | Any command starting with `sudo` |
| `SYSTEM_SHUTDOWN` | `shutdown`, `reboot`, `poweroff`, `halt` |
| `HISTORY_CLEAR` | `history -c`, `rm ~/.bash_history` |

## Man Vault

Man Vault is a local SQLite+FTS5 index of system man pages (~24,000 documents) that provides:
- Fast lexical search via FTS5 with BM25 ranking
- Semantic search via Ollama embeddings
- Hybrid search combining both approaches
- Snippet extraction for LLM context
- Flag verification for command validation

### Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Man Vault                         │
├─────────────────────────────────────────────────────┤
│  Indexer          │  Embedder         │  Retriever  │
│  - discover       │  - embed_text     │  - search   │
│  - render         │  - embed_batch    │  - snippet  │
│  - chunk          │  - cosine_sim     │  - flag     │
├─────────────────────────────────────────────────────┤
│                  Store (CRUD)                        │
├─────────────────────────────────────────────────────┤
│  SQLite + FTS5 (manvault.db)                        │
│  - docs (full text)                                  │
│  - docs_fts (FTS5 index)                            │
│  - chunks (for embeddings)                          │
│  - embeddings (vectors)                             │
└─────────────────────────────────────────────────────┘
```

### Key Files

| File | Purpose |
|------|---------|
| `daemon/manvault/models.py` | Pydantic models: ManDoc, ManChunk, ManSnippet, ManVaultStatus |
| `daemon/manvault/schema.py` | SQLite schema, migrations, triggers |
| `daemon/manvault/store.py` | CRUD operations with batch support |
| `daemon/manvault/indexer.py` | Discovery, rendering, chunking, indexing |
| `daemon/manvault/embedder.py` | Embedding generation via Ollama |
| `daemon/manvault/retriever.py` | Hybrid search, snippets, flag verification |
| `daemon/manvault/service.py` | Background indexing daemon service |

### Database Schema

```sql
-- Full man pages
CREATE TABLE docs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,           -- e.g., "ls"
    section TEXT NOT NULL,        -- e.g., "1"
    lang TEXT DEFAULT 'en',
    source_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,         -- For change detection
    text TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(name, section, lang)
);

-- FTS5 index
CREATE VIRTUAL TABLE docs_fts USING fts5(
    name, section, text,
    content='docs', content_rowid='id',
    tokenize='porter unicode61'
);

-- Chunks for embeddings
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER REFERENCES docs(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    heading TEXT,                 -- Section heading
    UNIQUE(doc_id, chunk_index)
);

-- Embeddings
CREATE TABLE embeddings (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL,      -- float32 array
    model TEXT NOT NULL,
    dim INTEGER NOT NULL
);
```

### CLI Commands

```bash
# Search documentation (hybrid search)
elle man list files

# Show index status
elle man status

# Trigger background reindexing
elle man reindex
```

### Search Types

| Type | Description | Speed |
|------|-------------|-------|
| `lexical` | FTS5 with BM25 ranking | < 20ms |
| `semantic` | Embedding similarity | < 100ms |
| `hybrid` | Reciprocal Rank Fusion of both | < 150ms |

### Embedding Models

- **Primary:** `nomic-embed-text` (768 dims, fast, good quality)
- **Fallbacks:** `all-minilm` (384 dims), `mxbai-embed-large` (1024 dims)

### Core Commands Seeding

On startup, Man Vault seeds essential system commands to ensure critical documentation is immediately available:

```python
CORE_COMMANDS = [
    # System services
    ("systemctl", "1"), ("journalctl", "1"),
    # Networking
    ("ufw", "8"), ("netplan", "5"), ("ip", "8"), ("ss", "8"), ("curl", "1"),
    # Containers
    ("docker", "1"),
    # Package management
    ("apt", "8"), ("snap", "8"),
    # Hardware monitoring
    ("smartctl", "8"), ("sensors", "1"),
    # Disk and filesystem
    ("df", "1"), ("du", "1"), ("lsof", "8"), ("fstab", "5"),
    # Process management
    ("ps", "1"), ("top", "1"), ("kill", "1"),
    # Text processing
    ("grep", "1"), ("sed", "1"), ("awk", "1"),
    # File operations
    ("ls", "1"), ("tar", "1"), ("find", "1"),
    # ... and 40+ more essentials
]
```

### Background Service

The `ManVaultService` runs as part of `elled` daemon:
1. **Startup**: Seeds core commands first (fast, ~60 commands)
2. **Initial**: Full indexing if database is empty
3. **Hourly**: Check for pending embeddings
4. **Daily**: Incremental indexing for package updates

```python
# In elled main
async def main():
    manvault_service = get_service()
    await manvault_service.start()  # Seeds core commands automatically
    # ...
    await manvault_service.stop()
```

### Public API

```python
from elle.daemon.manvault import search, get_document, flag_exists, get_status

# Hybrid search
results = search("list directory contents", k=5)

# Get full document
text = get_document("ls", "1")

# Verify flag exists
if flag_exists("ls", "-l"):
    ...

# Get index status
status = get_status()
```

## LLM Interface

ELLE uses a two-tier LLM architecture:

1. **`rag/ollama_client.py`** - Low-level HTTP client for classification and embeddings
2. **`rag/llm.py`** - High-level interface for general-purpose LLM interactions

### Default Model

**Primary:** `qwen2.5:7b-instruct` (Qwen2.5-7B-Instruct)

**Fallbacks:** `llama3.1:8b-instruct-q4_0`, `mistral:7b-instruct`, `gemma2:9b`

### Configuration Defaults

| Setting | Classification | General LLM |
|---------|---------------|-------------|
| Timeout | 30s | 120s |
| Max Tokens | 150 | 2048 |
| Temperature | 0.1 | 0.7 |

### Usage

```python
from elle.rag.llm import LLM, get_llm

llm = get_llm()
if llm.is_available():
    # Text generation
    response = llm.generate("Explain how to check disk usage on Ubuntu")
    print(response.content)

    # JSON generation (with automatic retry on parse error)
    data = llm.generate_json(
        "List the top 3 commands for checking disk space",
        schema={"commands": ["str"]}
    )

    # Chat-style with message history
    response = llm.chat([
        {"role": "system", "content": "You are a Linux expert."},
        {"role": "user", "content": "How do I restart nginx?"}
    ])
```

### JSON Mode

JSON generation includes automatic retry on parse failure:
1. First attempt with `temperature=0.7`
2. If JSON parsing fails, retry with `temperature=0.1` and explicit correction
3. Raises `LLMJSONError` if retry also fails

```python
# With schema hint
data = llm.generate_json(
    "Extract command info",
    schema={"name": "str", "flags": ["str"], "description": "str"}
)

# Chat-style JSON
data = llm.chat_json([
    {"role": "user", "content": "What are the mount options for ext4?"}
], schema={"options": ["str"]})
```

### Error Handling

```python
from elle.rag.llm import LLMError, LLMUnavailableError, LLMTimeoutError, LLMJSONError

try:
    response = llm.generate("...")
except LLMUnavailableError:
    # Ollama not running
except LLMTimeoutError:
    # Request timed out (increase timeout or reduce max_tokens)
except LLMJSONError as e:
    # JSON parsing failed
    print(e.raw_content)  # The raw response that failed to parse
except LLMError as e:
    # Other API errors
    print(e.status_code)
```

## Incident Vault

The Incident Vault is ELLE's decision memory - a durable store of incident reports that captures what happened, what was done, and the outcome. It enables learning from past incidents to guide future decisions.

### Purpose

An Incident Report is a durable artifact that captures:
- **What happened**: Signals, context, symptoms
- **What we decided**: Hypothesis, chosen plan, preconditions
- **What we did**: Commands, edits, actions taken
- **What happened after**: Measured outcome, verification results
- **What state we were in**: System snapshot (pre/post)
- **How to match this again**: Fingerprint, similarity features

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Incident Vault                           │
├─────────────────────────────────────────────────────────────┤
│  Correlator         │  Retriever          │  Service        │
│  - detect domain    │  - fingerprint      │  - embed        │
│  - extract entities │  - lexical (FTS5)   │  - maintain     │
│  - group events     │  - semantic         │                 │
├─────────────────────────────────────────────────────────────┤
│  Snapshot           │  Preconditions      │  Store (CRUD)   │
│  - collect state    │  - evaluate         │  - incidents    │
│  - extract features │  - infer            │  - actions      │
│  - diff snapshots   │  - match ratio      │  - snapshots    │
├─────────────────────────────────────────────────────────────┤
│  SQLite + FTS5 (incidents.db)                               │
│  - incidents, incident_actions, incident_snapshots          │
│  - incident_events, incident_embeddings, incidents_fts      │
└─────────────────────────────────────────────────────────────┘
```

### Key Files

| File | Purpose |
|------|---------|
| `daemon/incidents/models.py` | Pydantic models: IncidentReport, SystemSnapshot, Fingerprint, etc. |
| `daemon/incidents/schema.py` | SQLite schema with FTS5 for lexical search |
| `daemon/incidents/store.py` | CRUD operations: create, update, append_action, attach_snapshot |
| `daemon/incidents/snapshot.py` | System state collection: OS, CPU, memory, disk, network, docker |
| `daemon/incidents/preconditions.py` | DSL for evaluating conditions against current state |
| `daemon/incidents/correlator.py` | Event-to-incident grouping logic |
| `daemon/incidents/retriever.py` | Multi-tier similarity search (fingerprint, FTS5, semantic) |
| `daemon/incidents/service.py` | Background embedding and maintenance |

### Incident Lifecycle

```
Event/Failure → Create Draft → Attach Snapshot → Take Actions → Finalize Outcome
     │               │              │                │              │
     └───────────────┴──────────────┴────────────────┴──────────────┘
                               Append-only
```

1. **Trigger**: Telemetry event, command failure, or user task
2. **Draft**: Create incident with title, domain, severity
3. **Snapshot**: Capture pre-action system state
4. **Actions**: Log each command/edit executed
5. **Outcome**: Verify, set outcome (improved/partial/no_change/worse)

### Domain Categories

| Domain | Description | Detection Patterns |
|--------|-------------|-------------------|
| `net` | Network issues | connection, eth, timeout, DNS |
| `disk` | Disk/storage | I/O error, SMART, disk full |
| `oom` | Out-of-memory | OOM killer, memory pressure |
| `docker` | Container issues | container, docker |
| `auth` | Authentication | failed password, permission denied |
| `pkg` | Package management | apt, dpkg, dependency |
| `service` | Systemd services | service failed, unit |
| `fs` | Filesystem | mount, fstab, ext4 |

### System Snapshot

Captures point-in-time system state:

```python
class SystemSnapshot(BaseModel):
    os: str                    # "Ubuntu 24.04"
    kernel: str                # "6.8.0"
    uptime_sec: int
    cpu_load: tuple[float, float, float]  # 1min, 5min, 15min
    mem_total_mb: int
    mem_available_mb: int
    disks: tuple[dict, ...]    # mount, used_pct, avail_gb
    interfaces: tuple[dict, ...]  # name, state, errors
    services: tuple[dict, ...]    # name, active, failed
    docker_running: int
    docker_exited: int
    temps: tuple[dict, ...]    # sensor, celsius
    smart: tuple[dict, ...]    # dev, health, pct_used
```

### Fingerprint Features

Derived from snapshots for fast matching:

```python
class Fingerprint(BaseModel):
    disk_pressure: float       # Max disk usage (0-1)
    mem_pressure: float        # Memory pressure (0-1)
    cpu_pressure: float        # Load average
    oom_count_1h: int          # OOM kills last hour
    service_failures_1h: int
    entities: tuple[str, ...]  # service:nginx, interface:eth0
```

### Precondition DSL

Conditions that must match for an incident to be applicable:

```python
# Expressions
"disk./.used_pct > 95"          # Disk root > 95%
"services.nginx.failed == true"  # nginx in failed state
"mem_pressure > 0.8"            # High memory pressure
"docker.exited > 0"             # Exited containers

# Evaluate
ratio, results = evaluate_preconditions(preconditions, snapshot, fingerprint)
if ratio >= 0.6:  # At least 60% match
    # Incident solution may be applicable
```

### Multi-Tier Search

1. **Tier A (Fingerprint)**: Fast filter by domain, entity overlap, pressure similarity
2. **Tier B (Lexical)**: FTS5 search over title, summary, symptoms
3. **Tier C (Semantic)**: Embedding similarity (if Ollama available)

Results ranked by: `similarity × outcome_weight × precondition_match`

### Usage

```python
from elle.daemon.incidents import (
    create_incident_draft,
    append_action,
    attach_snapshot,
    finalize_outcome,
    collect_snapshot,
    search,
    get_prior_art,
)

# Create incident from failed command
incident = create_incident_draft(
    title="apt update failed",
    domain="pkg",
    trigger_source="command_failure",
    trigger_command="apt update",
)

# Attach pre-state
snapshot = collect_snapshot()
attach_snapshot(incident.incident_id, "pre", snapshot)

# Log action taken
append_action(
    incident.incident_id,
    kind="shell",
    command="apt update --fix-missing",
    exit_code=0,
    success=True,
)

# Search for similar past incidents
results = search(
    query="apt update failed dependency",
    domain="pkg",
    k=3,
)

# Get prior art for LLM prompt injection
prior = get_prior_art(
    query="apt update failed, dependency issues",
    domain="pkg",
    fingerprint=current_fingerprint,
    snapshot=current_snapshot,
)
# Returns: [{incident_id, title, outcome, decision, root_cause, ...}, ...]

# Finalize with outcome
finalize_outcome(
    incident.incident_id,
    outcome="improved",
    verification_steps=["apt update succeeded", "packages installed"],
    root_cause="Mirror was temporarily unavailable",
)
```

### Prior Art Injection

When building prompts for fixit/system_task, inject similar past incidents:

```
Similar Incident #1: "apt update failed - mirror timeout"
- Outcome: improved
- Decision: {"plan": "retry with different mirror", "commands": [...]}
- Root Cause: "Primary mirror was down"

Consider this prior art when the current state matches similar preconditions.
```

### Database Location

- **Path**: `/var/lib/elle/incidents.db`
- **Tables**: incidents, incident_actions, incident_snapshots, incident_events, incident_embeddings
- **FTS5**: incidents_fts (title, summary, symptoms, root_cause, tags)

## File Operations

ELLE provides safe file operations with preview, atomic execution, and rollback support.

### Key Files

| File | Purpose |
|------|---------|
| `ops/files/models.py` | Pydantic models: FileOp, ReadResult, WriteResult |
| `ops/files/handler.py` | FileHandler with preview/execute/rollback |
| `ops/files/text.py` | TextHandler and MarkdownBuilder |
| `ops/files/organizer.py` | Smart file organization by type/date |

### Read/Write Operations

```python
from elle.ops.files import read_file, write_file, ReadResult, WriteResult

# Read a file
result: ReadResult = read_file("/etc/hostname")
if result.success:
    print(result.content)
    print(f"Size: {result.size_bytes} bytes")

# Write a file (fails if exists without overwrite=True)
result: WriteResult = write_file(
    path="/tmp/config.txt",
    content="key=value",
    overwrite=False,  # Safe default
)
if result.success:
    print(f"Wrote {result.bytes_written} bytes")
    print(f"Created new file: {result.created}")
```

### FileOp Model

```python
class FileOp(BaseModel):
    kind: Literal["move", "copy", "delete", "rename", "mkdir", "read", "write"]
    source: str                    # Source path
    dest: str | None = None        # Destination (move/copy/rename)
    content: str | None = None     # Content (write)
    encoding: str = "utf-8"        # File encoding (read/write)
    recursive: bool = False        # For directories
    overwrite: bool = False        # Allow overwriting
```

### TextHandler

Higher-level text file operations:

```python
from elle.ops.files import TextHandler, get_text_handler

handler = get_text_handler()

# Append to file
result = handler.append(path, "New line", separator="\n")

# Prepend to file
result = handler.prepend(path, "Header")

# Replace section between markers
result = handler.replace_section(
    path,
    marker="<!-- CONFIG -->",
    content="new config here",
)
```

### MarkdownBuilder

Fluent API for building markdown content:

```python
from elle.ops.files import MarkdownBuilder

doc = (
    MarkdownBuilder()
    .heading("Installation Guide", level=1)
    .paragraph("Follow these steps:")
    .list_items(["Step 1", "Step 2", "Step 3"])
    .code_block("pip install elle", language="bash")
    .table(
        headers=["Option", "Description"],
        rows=[["--verbose", "Enable verbose output"]],
    )
    .build()
)

# Or save directly to file
result = MarkdownBuilder().heading("Report").save("/tmp/report.md")
```

## LLM-Driven Config Generation

The `confgen` module generates configuration changes from natural language requests using the local LLM.

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Config Generator                     │
├─────────────────────────────────────────────────────────┤
│  Service           │  Generator        │  Validator     │
│  - generate()      │  - build_context  │  - validate    │
│  - apply()         │  - call LLM       │  - path safety │
├─────────────────────────────────────────────────────────┤
│  Domain Handlers                                         │
│  - NetplanHandler  - FstabHandler   - SshdHandler       │
│  - SystemdHandler  - CronHandler    - HostsHandler      │
├─────────────────────────────────────────────────────────┤
│  Prompts & Templates                                     │
│  - Edit mode (modify existing)                          │
│  - Create mode (new file)                               │
│  - Patch mode (targeted changes)                        │
└─────────────────────────────────────────────────────────┘
```

### Key Files

| File | Purpose |
|------|---------|
| `rag/confgen/models.py` | ConfigGenRequest, ConfigOp, ConfigGenResult, ConfigGenOutcome |
| `rag/confgen/prompts.py` | System prompts, edit/create templates, JSON schemas |
| `rag/confgen/domains.py` | Domain handlers with validation and schema hints |
| `rag/confgen/generator.py` | LLMConfigGenerator - builds context, calls LLM |
| `rag/confgen/validator.py` | Pre-apply validation (path safety, syntax, injection) |
| `rag/confgen/service.py` | ConfigGenService - high-level orchestration |

### Usage

```python
from elle.rag.confgen import generate, get_confgen_service

# Simple generation
outcome = generate(
    request="Set static IP 192.168.1.10 for eth0",
    target_path="/etc/netplan/01-netcfg.yaml",
)

if outcome.success:
    # Preview the change
    print(outcome.result.title)
    print(outcome.result.explanation)

    for op in outcome.result.operations:
        print(f"  {op.kind} {op.path} = {op.value}")

    print(f"Risks: {outcome.result.risks}")
    print(f"Validation command: {outcome.result.validation_command}")

    # Apply if approved
    service = get_confgen_service()
    outcome = service.apply(outcome)

    if outcome.applied:
        print(f"Applied! Backup at: {outcome.backup_path}")
```

### Create Mode

Generate new configuration files:

```python
outcome = generate(
    request="Create a netplan config for DHCP on enp0s3",
    target_path="/etc/netplan/99-elle.yaml",
    mode="create",
)

if outcome.success:
    print(outcome.result.generated_content)
    # network:
    #   version: 2
    #   renderer: networkd
    #   ethernets:
    #     enp0s3:
    #       dhcp4: true
```

### Domain Handlers

Each domain handler provides:
- **File patterns** - Auto-detection from path
- **Schema hints** - Structure hints for LLM
- **Validation** - Domain-specific validation rules
- **Templates** - Default content for new files
- **Validation commands** - How to verify the config

```python
from elle.rag.confgen import detect_domain, detect_file_type, get_handler

# Auto-detect domain from path
domain = detect_domain("/etc/netplan/config.yaml")  # "network"
file_type = detect_file_type("/etc/netplan/config.yaml")  # "yaml"

# Get domain handler
handler = get_handler("ssh")
schema = handler.get_schema_hint("/etc/ssh/sshd_config")
validation_cmd = handler.get_validation_command("/etc/ssh/sshd_config")  # "sshd -t"
```

### Supported Domains

| Domain | File Patterns | Type | Validation Command |
|--------|--------------|------|-------------------|
| `network` | `/etc/netplan/*.yaml` | yaml | `netplan try` |
| `filesystem` | `/etc/fstab` | augeas | `mount -a --fake` |
| `ssh` | `/etc/ssh/sshd_config*` | augeas | `sshd -t` |
| `firewall` | `/etc/ufw/*` | text | `ufw status verbose` |
| `service` | `/etc/systemd/system/*.service` | systemd | `systemd-analyze verify` |
| `cron` | `/etc/crontab`, `/etc/cron.d/*` | text | - |
| `hosts` | `/etc/hosts` | augeas | - |

### Key Models

```python
class ConfigOp(BaseModel):
    kind: Literal["set", "rm", "ins", "mv", "append", "replace"]
    path: str              # Augeas or dot-notation path
    value: str | None      # New value
    explanation: str       # Why this operation

class ConfigGenResult(BaseModel):
    title: str             # Short description
    explanation: str       # Detailed explanation
    operations: tuple[ConfigOp, ...]  # For edit mode
    generated_content: str | None     # For create mode
    target_path: str
    file_type: ConfigFileType
    requires_privilege: bool
    validation_command: str | None
    risks: tuple[str, ...]
    rollback_hint: str | None
    grounded_in: tuple[str, ...]  # Man pages used

class ConfigGenOutcome(BaseModel):
    request: ConfigGenRequest
    result: ConfigGenResult | None
    validation: ConfigGenValidation | None
    applied: bool
    backup_path: str | None

    @property
    def success(self) -> bool: ...
```

### Validation

Pre-apply validation checks:
- **Path safety** - Blocks /etc/passwd, /boot, etc.
- **Syntax validation** - YAML, JSON, TOML parsing
- **Shell injection detection** - Blocks $(), ``, ; patterns
- **Domain-specific rules** - Per-handler validation
- **Rollback requirements** - Critical files need rollback hints

```python
from elle.rag.confgen import validate, ConfigGenValidation

validation: ConfigGenValidation = validate(result)
if not validation.valid:
    for issue in validation.issues:
        print(f"{issue.severity}: {issue.message}")
        if issue.suggestion:
            print(f"  Suggestion: {issue.suggestion}")
```

### Safety Features

1. **Low temperature (0.2)** - Precise config generation
2. **Man Vault grounding** - Uses documentation for correct syntax
3. **Incident Vault prior art** - Learns from past changes
4. **Mandatory preview** - Always show changes before apply
5. **Automatic backups** - All changes backed up to `/var/lib/elle/backups/`
6. **Forbidden paths** - Cannot modify /etc/passwd, /etc/shadow, /boot
