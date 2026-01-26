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
    docker/                # Container diagnostics, compose conversion, env detection
    network/               # Connectivity diagnosis, firewall explanation
    reactive_commands.py   # /react REPL commands
    map_commands.py        # /map REPL commands for GUI automation (renamed from learn)
    package_learn_commands.py  # /learn REPL commands for package capability generation
    setup/                 # Setup wizard (models.py, wizard.py)
  daemon/
    telemetry/             # Journal/kernel watchers, probes, eBPF
      docker_watcher.py    # Container state change monitoring
      inotify_watcher.py   # File system change monitoring
      port_probe.py        # Network port status monitoring
      package_probe.py     # Package install/upgrade detection, auto-learning trigger
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
    constants.py           # LLM model configuration constants
    model_warmup.py        # Model preloading and warmup
    confgen/               # LLM-driven config generation (netplan, docker, wireguard)
    prompts/               # Intent-specific prompt segments
  atspi/                   # AT-SPI GUI automation
    models.py              # UIElement, UIRecipe, UIAction, UITaskPlan
    client.py              # AT-SPI bus connection and tree traversal
    store.py               # Recipe CRUD operations (recipes.db)
    learner.py             # Application UI learning
    matcher.py             # Fuzzy element matching with self-healing
    planner.py             # LLM-based task planning
    executor.py            # UI action execution with incident integration
  capabilities/            # Typed operations with policy enforcement
    core/gui.py            # GUI capabilities (gui.learn, gui.click, gui.type, etc.)
    autogen/               # Capability auto-generation from packages
      __init__.py          # High-level API (generate_and_save, load_capabilities)
      discovery.py         # Binary and package discovery
      parser.py            # Man page parsing
      generator.py         # LLM-based capability spec generation
      factory.py           # Code generation for capability classes
      validator.py         # Validation stages including package coherence
      store.py             # SQLite storage for generated capabilities
      loader.py            # Load and register capabilities at runtime
      versioner.py         # Package version tracking, capability regeneration
      bootstrap.py         # Core package capability generation on first run
      intelligence/        # Multi-source intelligence extraction
        models.py          # PackageIntelligence, ExtractedFlag, etc.
        extractors.py      # dpkg, bash/zsh completion, man, systemd extractors
        aggregator.py      # Combine sources with token budget
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

## CRITICAL: Daemon & Capability Primacy

**THIS SECTION IS NON-NEGOTIABLE. Any deviation requires explicit petition to the user with a written justification explaining why the core architecture cannot support the requirement.**

ELLE's architecture rests on two foundational pillars that must never be bypassed:

### 1. The Daemon Owns ALL Passive Monitoring

The daemon (`elled`) is the **sole owner** of all passive system observation. Period. Full stop.

- **Telemetry ingestion**: Journal, kernel, eBPF, inotify, probes - ALL flow through the daemon
- **Event correlation**: The daemon normalizes, deduplicates, and fingerprints events
- **State tracking**: System state changes are detected and recorded by the daemon
- **No CLI polling**: The CLI never polls or monitors - it queries the daemon or reacts to daemon events

**Why this matters:** Centralized observation enables consistent event handling, prevents race conditions, reduces system load, and ensures complete audit trails.

### 2. Capabilities Are Atomic Building Blocks

Capabilities are **not** wrappers around shell commands. They are typed, policy-governed, auditable operations that serve as the fundamental units of system mutation.

**Capabilities must:**
- Have explicit input/output schemas (Pydantic models)
- Declare their risk level and side effects
- Be governed by the Policy Engine
- Produce evidence of execution for incident records
- Be composable by the Planner

**The Planner constructs plans from Capabilities**, not from raw commands. Plans are recorded in Incident Reports with full provenance, enabling:
- Pattern matching against prior decisions
- Rollback through capability reversal
- Policy enforcement at every step

### 3. New Functionality Must Emerge from Core Systems

When new functionality is requested, it **must** come through one of these channels:

| Channel | Examples |
|---------|----------|
| **Daemon upgrades** | New watchers, probes, eBPF programs, telemetry sources |
| **New Capabilities** | Typed operations added to the registry |
| **Planner improvements** | Better retrieval, synthesis, or plan generation |
| **Core data objects** | Extended models for Session, Incident, TelemetryEvent |
| **Reactive Functions** | User-defined automations that compose capabilities |

### 4. "Arms" Are an Absolute Last Resort

An "arm" is a hardcoded flow that bypasses the capability system - direct shell commands, bespoke handlers, or domain-specific modules that don't integrate with the core.

**Arms may only be built when:**
- There is a **specific safety or reliability reason** the core cannot be trusted
- The justification is **documented in this file** or in code comments
- The user has **explicitly approved** the deviation

**Expected frequency:** Exceedingly rare. If you find yourself building arms regularly, the core architecture needs improvement, not workarounds.

### Petition Process

To supersede this principle:

1. **State the requirement** that cannot be met by the core
2. **Explain why** daemon/capabilities/planner cannot support it
3. **Propose the deviation** with scope and boundaries
4. **Document the tradeoff** - what is lost by bypassing the core
5. **Obtain explicit user approval** before implementation

---

## Self-Building Architecture

ELLE is designed to **build itself** rather than accumulate discrete feature modules. New functionality should expand the core systems - not extend from them as separate arms.

**The core systems that grow:**
- **Capabilities Registry** - New operations become typed capabilities, not one-off scripts
- **Reactive Functions** - Automations are user-defined and machine-learned, not hard-coded
- **Man Vault** - Knowledge expands based on what the user asks about and uses
- **Incident Vault** - Decision memory grows from experience, enabling pattern matching
- **Policy Engine** - Rules evolve based on user preferences and risk tolerance

**When adding functionality:**
1. **Ask first:** Can this be a Capability? A Reactive Function? A Man Vault entry?
2. **Expand the core:** Add to the registry/vault/engine rather than building a standalone module
3. **Make it learnable:** New patterns should be discoverable and reusable by the system
4. **User-specific:** The system should adapt to THIS user on THIS machine, not generic defaults

**Anti-patterns to avoid:**
- Building bespoke handlers that bypass the capabilities system
- Hard-coding automations that could be reactive functions
- Creating domain-specific modules when a generic capability would suffice
- Adding features that don't integrate with the existing memory/learning systems

**The goal:** Over time, ELLE learns from incidents, accumulates capabilities, builds reactive automations, and indexes knowledge - becoming increasingly capable through use rather than through code additions.

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
| `gui_task` | GUI automation request (AT-SPI) |
| `learn_package` | Package capability learning (`/learn`, "figure out how to use ffmpeg") |
| `fixit` | Repair a failed command |
| `navigation` | `status`, `events`, `logs`, `/map` |
| `meta` | `help`, `exit`, `config` |

**Classification precedence:**
1. Hard keyword routes (exact matches)
2. Prefix commands (`/ask`, `/do`, `/sh`, `/fix`, `/learn`, `/map`, `!`)
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
- UI Recipes: `/var/lib/elle/recipes.db`
- Generated Capabilities: `/var/lib/elle/autogen.db`
- Bootstrap State: `/var/lib/elle/bootstrap_state.json`
- Docker Env Store: `/var/lib/elle/docker_env.db`
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
- `CapabilityDomain` - service, file, config, network, package, docker, auth, gui
- `SideEffectKind` - includes `ui_interaction`, `window_focus` for GUI ops

**Pattern:** Capabilities are registered in `CapabilityRegistry`, executed via `CapabilityExecutor` which enforces policy.

## Capability Auto-Generation (`capabilities/autogen/`)

Automatic capability generation from installed packages using multi-source intelligence extraction.

### Architecture

```
/learn <package>
        │
        ▼
┌─────────────────────┐
│ PackageIntelligence │  ← Multi-source extraction
│     Aggregator      │
└─────────────────────┘
        │
┌───────┴───────────────────────────┐
│       │       │       │           │
▼       ▼       ▼       ▼           ▼
dpkg  bash/zsh  man    systemd    --help
meta  completions page  units     output
        │
        ▼
┌─────────────────────┐
│  LLM Generation     │  ← PACKAGE_CAPABILITY_GENERATION_SEGMENT
│  (with schema)      │
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│  Validator          │  ← Includes PACKAGE_COHERENCE stage
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│  AutogenStore       │  ← SQLite with approval workflow
└─────────────────────┘
```

### Extractors (by priority)

| Extractor | Priority | Source | Signal Quality |
|-----------|----------|--------|----------------|
| DpkgMetadataExtractor | 10 | dpkg-query | Metadata |
| FileManifestExtractor | 20 | dpkg -L | File paths |
| BashCompletionExtractor | 30 | /usr/share/bash-completion | HIGH |
| ZshCompletionExtractor | 31 | /usr/share/zsh/vendor-completions | HIGH |
| ManPageExtractor | 40 | Existing parser | Medium |
| SystemdUnitExtractor | 50 | /lib/systemd/** | Medium |
| HelpOutputExtractor | 70 | --help fallback | Low |

### Key Models

```python
class ExtractedFlag(BaseModel):
    flag: str                    # e.g., "-v", "--verbose"
    long_form: str | None
    description: str
    takes_value: bool
    value_type: str | None       # "file", "int", "string"
    source: str                  # "man", "completion", "help"
    confidence: float

class PackageIntelligence(BaseModel):
    package_name: str
    metadata: PackageMetadata
    manifest: FileManifest
    all_flags: tuple[ExtractedFlag, ...]
    subcommands: tuple[ExtractedSubcommand, ...]
    completions: ShellCompletions | None
    systemd_units: tuple[SystemdUnitInfo, ...]
    extraction_sources: tuple[str, ...]
    token_estimate: int
```

### Validation Stages

`ValidationStage` enum includes:
- `FLAGS` - Verify flags exist in man page
- `DRY_RUN` - Test --dry-run/--check options
- `SANDBOX` - Test in restricted environment
- `PACKAGE_COHERENCE` - Validate against PackageIntelligence

### Bootstrap & Auto-Learning

**Bootstrap:** On daemon startup, if `capability_bootstrap_enabled` (default: True):
- Runs once per ELLE version
- Generates capabilities for ~30 core + ~25 optional packages
- State stored in `/var/lib/elle/bootstrap_state.json`

**Auto-Learn:** When `auto_learn_new_packages` (default: True):
- PackageProbe detects new package installations
- Triggers `_auto_learn_package()` callback
- Runs learning in background without blocking

### REPL Commands

```bash
/learn <package>        # Learn specific package
/learn --all            # Learn ALL installed packages
/learn --all --dry-run  # Preview what would be learned
/learn list             # List packages with capabilities
/learn show <package>   # Show capabilities for package
/learn approve <name>   # Approve capability for use
/learn bootstrap        # Run core package bootstrap
/learn status           # Show bootstrap status
```

### Configuration

```toml
[daemon]
capability_versioning_enabled = true   # Regenerate on package upgrade
package_probe_interval = 300           # 5 minutes
capability_bootstrap_enabled = true    # Bootstrap on first run
auto_learn_new_packages = true         # Auto-learn new installs
```

Environment variables: `ELLE_CAPABILITY_VERSIONING_ENABLED`, `ELLE_AUTO_LEARN_NEW_PACKAGES`

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

## AT-SPI GUI Automation (`atspi/`)

GUI automation via Linux accessibility APIs (AT-SPI2).

**Key models:**
- `UIElement` - Single accessible UI component with path, role, name, states
- `UIRecipe` - Versioned recipe for an application's UI structure
- `UIAction` - Single action: click, type, toggle, scroll, wait
- `UITaskPlan` - Sequence of actions for a user request
- `ExecutionResult` - Outcome with adaptation notes

**Matching strategies** (in order of preference):
1. `direct_path` - Use recorded element path (fastest, 1.0 confidence)
2. `exact_name` - Search by exact name match (0.95 confidence)
3. `fuzzy_name` - Levenshtein distance matching (0.85 confidence)
4. `sibling_context` - Find element near known siblings (0.85 confidence)
5. `role_search` - Find by role and partial name (0.75 confidence)
6. `tree_search` - Full tree traversal (0.65 confidence)

**REPL commands:** `/map <app>`, `/map list`, `/map show <app>`, `/map rebuild <app>`, `/map delete <app>`

**Note:** GUI learning was renamed from `/learn` to `/map`. The `/learn` command is now used for package capability generation.

**GUI task patterns** (in classifier):
- `(?:open|launch|start)\s+(\w+)\s+and\s+(.+)` → gui_task
- `(?:click|press|toggle|enable|disable)\s+(.+)\s+in\s+(\w+)` → gui_task
- `in\s+(\w+),?\s+(?:click|toggle|enable|disable)\s+(.+)` → gui_task

## Prompt Segments (`rag/prompts/`)

Intent-specific prompt augmentation system.

**Key classes:**
- `PromptSegment` - Atomic prompt content for a specific intent
- `PromptComposer` - Combines base prompt + matching segments

**Default segments:**
- `reactive_function` - Reactive function creation guidance
- `capability_discovery` - Capability system explanation
- `package_capability_generation` - Package capability generation with strict JSON schema
- `config_generation` - Config editing guidelines
- `docker_operations` - Docker best practices
- `fixit` - Command repair guidance
- `incident_analysis` - Incident investigation guidance
- `network_diagnostics` - Network troubleshooting
- `wireguard` - VPN configuration
- `service_management` - Systemd services
- `gui_automation` - AT-SPI GUI automation guidance

**Usage:** Segments are auto-selected based on classified intent.

## LLM Configuration (`rag/constants.py`)

Centralized model configuration:

**SLM (Classification):**
- Model: `phi3.5:3.8b-mini-instruct-q8_0`
- Keep-alive: `-1` (never unload)
- Context: 2048 tokens
- Temperature: 0.1

**LLM (Generation):**
- Model: `qwen2.5:7b-instruct-q8_0`
- Keep-alive: `10m`
- Context: 32768 tokens
- Temperature: 0.7

**Context management:**
- Compact threshold: 80% of context window
- Compact target: 60% of context window
- Min messages: 8

## Docker Environment Detection (`cli/docker/`)

Smart environment variable detection for Docker containers.

**Components:**
- `env_detector.py` - Detects required env vars from image/CLI
- `env_profiles.py` - Known profiles for common images (postgres, mysql, redis)
- `env_prompt.py` - Interactive prompting for missing values
- `env_store.py` - Secure storage of env configurations
- `env_explainer.py` - LLM-powered env var explanation

**Domains:** `IncidentDomain` includes `gui` for GUI automation incidents.
