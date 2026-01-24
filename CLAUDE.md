# CLAUDE.md

## Project Overview

ELLE (Enabling Layer Learning Everything) is a local-first, agentic system layer for Ubuntu 24.04 LTS that converts kernel-level telemetry into natural language insight and safe system operations.

## Architecture

```
User ──▶ elle (CLI/REPL)
         ├─▶ Man Vault (SQLite+FTS5 documentation index)
         ├─▶ Incident Vault (decision memory + prior art)
         ├─▶ Ollama (local inference)
         └─▶ elled (daemon: telemetry + privileged ops)
```

**Components:**
- **elled** - Daemon: telemetry ingestion, Man Vault indexing, Incident Vault, Polkit helper
- **elle** - CLI: REPL and one-shot commands via shared Engine
- **Man Vault** - Local SQLite+FTS5 index of `/usr/share/man/**`
- **Incident Vault** - Decision memory storing incidents, actions, outcomes

## Repository Structure

```
src/elle/
  cli/
    engine.py              # Core command processing (shared REPL/one-shot)
    subprocess_runner.py   # Safe subprocess with denylist
    terminal/              # REPL, classifier, executor, renderer, intent
    fixit/                 # Command failure diagnosis and repair
    planner/               # Multi-step task planning
    docker/                # Container diagnostics, compose conversion
    network/               # Connectivity diagnosis, firewall explanation
    reactive_commands.py   # /react REPL commands
  daemon/
    telemetry/             # Journal/kernel watchers, probes, eBPF
    manvault/              # Man page indexing, search, embeddings
    incidents/             # Incident reports, snapshots, semantic_diff
    notifications/         # ntfy alerts
    reboot/                # Kernel update tracking
    api/                   # FastAPI bridge
  ops/
    files/                 # Safe file operations
    augeas/                # Config editing with preview/rollback
    wireguard/             # WireGuard VPN configuration
  rag/
    llm.py                 # High-level LLM interface
    ollama_client.py       # Low-level Ollama HTTP client
    confgen/               # LLM-driven config generation (netplan, docker, wireguard)
  capabilities/            # Typed operations with policy enforcement
  policy/                  # Rule-based access control engine
  reactive/                # Event-driven automation system
  security/                # Polkit integration
  common/                  # Session, models, db utilities
tests/                     # pytest test suite
```

## Development Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                    # Run tests
pytest --cov=elle         # With coverage
ruff check src/           # Lint
ruff format src/          # Format
mypy src/                 # Type check
```

## Core Design Principles

- **Local-first**: All inference via Ollama
- **No ambient sudo**: Privileged actions are discrete, auditable, Polkit-gated
- **Intent before execution**: Every input classified before action
- **Explain → Plan → Confirm → Apply**: Never silently mutate the system
- **Pydantic everywhere**: All data objects, inputs, outputs, API boundaries use Pydantic models

## Engine Architecture

All input flows through shared `Engine.process()`:

```
REPL / One-shot → Engine.process(input, session) → (EngineResult, new_session)
                         │
         ┌───────────────┼───────────────┐
         │               │               │
      Classify        Execute         Render
```

**Key patterns:**
- `Session` is **immutable** (frozen dataclass) - operations return new instances
- `EngineResult` includes: output, updated session, action signal, success flag
- `EngineAction` enum: `CONTINUE`, `EXIT`, `CLEAR`

## Intent Classification

Every input classified into exactly one intent before execution:

| Intent | Description |
|--------|-------------|
| `shell_passthrough` | Safe shell command |
| `system_question` | Explanation/diagnosis request |
| `system_task` | Requested system change |
| `fixit` | Repair a failed command |
| `navigation` | `status`, `events`, `logs` |
| `meta` | `help`, `exit`, `config` |

**Classification precedence:**
1. Hard keyword routes (exact matches)
2. Prefix commands (`/ask`, `/do`, `/sh`, `/fix`, `!`)
3. Pattern matching (regex)
4. SLM classification (Ollama fallback)
5. Safety overrides (reduce confidence for dangerous commands)

**Thresholds:** `HIGH=0.90`, `MEDIUM=0.75`, `MIN=0.55`

## Security Constraints

**Subprocess denylist** (blocked even in passthrough):
- `DESTRUCTIVE_RM` - `rm -rf /`, `rm -rf /etc`
- `FILESYSTEM_FORMAT` - `mkfs`, `mkswap`
- `RAW_DISK_WRITE` - `dd of=/dev/sda`
- `FORK_BOMB` - `:(){:|:&};:`
- `RECURSIVE_PERMISSION` - `chmod -R 777 /`
- `PIPE_TO_SHELL` - `curl ... | bash`
- `SUDO_ATTEMPT` - Any `sudo` command
- `SYSTEM_SHUTDOWN` - `shutdown`, `reboot`

**Config editing safety:**
- backup → apply via Augeas/temp → validate → commit/rollback
- Forbidden paths: `/etc/passwd`, `/etc/shadow`, `/boot`
- Shell injection detection for `$()`, backticks, `;`

## Key Data Models

```python
class TelemetryEvent(BaseModel):
    ts: datetime
    source: Literal["journal", "kernel", "probe", "ebpf"]
    severity: Literal["info", "warning", "error", "critical"]
    category: str
    message: str
    raw: dict
    fingerprint: str  # Deduplication

class CommandPlan(BaseModel):
    explanation: str
    commands: list[str]
    checks: list[str]
    rollback: list[str]
    risks: list[str]
    requires_privilege: bool

class ReactiveFunction(BaseModel):
    # Frozen Pydantic model
    id: str
    name: str
    trigger: Trigger  # event|schedule|manual
    condition: Condition | None  # JSONLogic expression
    actions: tuple[ActionSpec, ...]  # Capabilities to execute
    policy: PolicySpec  # max_frequency, require_confirmation, etc.

class CapabilitySpec(BaseModel):
    name: str  # e.g., "service.restart"
    domain: CapabilityDomain
    risk_level: RiskLevel
    side_effects: tuple[SideEffectKind, ...]
    input_schema: dict  # JSON Schema
```

## Storage Locations

- Event DB: `/var/lib/elle/elle.db`
- Man Vault: `/var/lib/elle/manvault.db`
- Incident Vault: `/var/lib/elle/incidents.db`
- Reactive Functions: `/var/lib/elle/reactive.db`
- Policy Rules: `/var/lib/elle/policy.db`
- Config backups: `/var/lib/elle/backups/<domain>/<timestamp>/`

## Module Responsibilities

### Man Vault (`daemon/manvault/`)
- SQLite+FTS5 with BM25 ranking for lexical search
- Semantic search via Ollama embeddings
- Hybrid search using Reciprocal Rank Fusion
- Seeds ~60 core commands on startup

### Incident Vault (`daemon/incidents/`)
Decision memory capturing: what happened, what we decided, what we did, outcome, system state (pre/post), fingerprint for matching.

**Lifecycle:** Event/Failure → Draft → Snapshot → Actions → Finalize Outcome

**Domain categories:** `net`, `disk`, `oom`, `docker`, `auth`, `pkg`, `service`, `fs`

**Multi-tier search:** Fingerprint filter → FTS5 lexical → Semantic embedding

### Fixit (`cli/fixit/`)
Command failure diagnosis via:
1. Exit code/stdout/stderr analysis
2. Incident Vault search for similar failures
3. Man Vault query for docs
4. LLM generation (or rule-based fallback)

**Rule-based fallbacks:** Permission denied, command not found, no space, connection refused, apt lock

### Planner (`cli/planner/`)
Multi-step execution plans with:
- Task complexity classification
- Man Vault + Incident Vault context
- LLM plan generation
- Step verification (pre/post conditions)
- Rollback steps

### Augeas (`ops/augeas/`)
Config editing with preview/execute/rollback:
- Auto-detect lens from file type
- Generate unified diffs
- Syntax + semantic validation
- Automatic backup rotation (keep last 10)

**Supported:** sshd_config, fstab, hosts, sudoers, cron, ini, YAML (custom handler)

### Config Generation (`rag/confgen/`)
LLM-driven config changes:
- Domain handlers: netplan, fstab, sshd, ufw, systemd, cron, hosts
- Edit mode (modify) vs Create mode (new file)
- Pre-apply validation (path safety, syntax, injection)
- Man Vault grounding for correct syntax

## LLM Interface

Two-tier architecture:
- `rag/ollama_client.py` - Low-level HTTP (classification, embeddings)
- `rag/llm.py` - High-level interface (generate, chat, JSON mode)

**JSON mode:** Auto-retry on parse failure with lower temperature

## Capabilities System (`capabilities/`)

Typed, policy-governed operations replacing raw shell commands.

**Key models:**
- `CapabilitySpec` - Name, domain, risk_level, side_effects, input/output schema
- `CapabilityResult` - Success/failure, evidence, duration
- `RiskLevel` - none, low, medium, high, critical
- `CapabilityDomain` - service, file, config, network, package, docker, auth

**Pattern:** Capabilities are registered in `CapabilityRegistry`, executed via `CapabilityExecutor` which enforces policy.

## Policy Engine (`policy/`)

Rule-based access control for capability execution.

**Effects:** `ALLOW`, `DENY`, `REQUIRE_CONFIRMATION`, `REQUIRE_JUSTIFICATION`, `REQUIRE_PREVIEW`, `AUDIT`

**Matchers:** exact, glob, regex, prefix matching on capability names, domains, risk levels

**Default rules:** High-risk requires confirmation, critical requires justification, file.delete requires preview

## Reactive Functions (`reactive/`)

Event-driven automations from natural language.

**Components:**
- `ReactiveFunction` - Trigger + Condition + Actions + Policy
- `ConditionEvaluator` - JSONLogic-style: `{gte: [{event.raw.used_pct}, 90]}`
- `ReactiveEngine` - Matches events, evaluates conditions, executes capabilities
- `ReactiveScheduler` - Cron-based triggers via croniter
- `ReactiveFunctionCompiler` - NL prompt → ReactiveFunction via LLM

**Trigger types:** `event` (telemetry), `schedule` (cron), `manual`

**Variables in conditions:** `{event.raw.X}`, `{state.Y}`, `{system.Z}`

**Rate limiting:** `max_frequency`, `max_daily_executions`, `allowed_hours`

**REPL commands:** `/react create|list|show|enable|disable|delete|history|test`

## Test Data

- `tests/intent_cases.jsonl` - ~200 intent classification cases
- `tests/fixit_cases.jsonl` - Common failure patterns
