# Architecture

## The Spine

All functionality in ELLE flows through a single execution pipeline called The Spine:

```
DAEMON → SIGNALS → INCIDENT REPORT → AGENT LOOP → CAPABILITIES → OUTCOME → INCIDENT MEMORY
```

This pipeline is enforced as an architectural constraint. There are no hardcoded diagnostic handlers, no direct subprocess calls that bypass capabilities, and no operations that skip the audit trail. If functionality doesn't flow through The Spine, it gets refactored or removed.

## The Three Pillars

### 1. The Daemon owns all passive monitoring

The daemon (`elled`) is the sole owner of all passive system observation:

- **Telemetry ingestion** — Journal, kernel, eBPF, inotify, probes all flow through the daemon
- **Event correlation** — Normalizes, deduplicates, and fingerprints events
- **State tracking** — System state changes detected and recorded by the daemon
- **No CLI polling** — The CLI never polls. It queries the daemon or reacts to daemon events.

### 2. Capabilities are the only way to mutate

Every system-modifying operation is a typed, policy-governed Capability:

- Explicit input/output schemas (Pydantic models)
- Declared risk level and side effects
- Governed by the Policy Engine
- Produces evidence for incident records
- Executed via `CapabilityExecutor`

The Agent Loop plans with Capabilities, not shell commands.

### 3. The Agent Loop orchestrates everything

The Agent Loop is the LLM-powered reasoning engine:

1. Creates an Incident Report for provenance
2. Retrieves context from Man Vault + Incident Vault
3. Reasons over the context with the LLM
4. Executes Capabilities (policy-enforced)
5. Records outcome to Incident Memory

## Data flow

```
User ──▶ elle (CLI/REPL)
         │
         ▼
┌───────────────────────────────────────────────────────┐
│                    AGENT LOOP                          │
│  1. Create Incident Report (provenance tracking)       │
│  2. Search Man Vault (documentation)                   │
│  3. Search Incident Vault (prior decisions)            │
│  4. LLM reasons over context                           │
│  5. Execute Capabilities (policy-enforced)             │
│  6. Record outcome to Incident Memory                  │
└───────────────────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────────────────┐
│                   DAEMON (elled)                       │
│  ├─▶ Telemetry: Journal, Kernel, eBPF, inotify        │
│  ├─▶ Man Vault: ~24,000 man pages indexed              │
│  ├─▶ Incident Vault: Decision memory + outcomes        │
│  ├─▶ Event correlation & fingerprinting                │
│  └─▶ Polkit helper for privileged operations           │
└───────────────────────────────────────────────────────┘
```

## Agent Loop tools

The Agent Loop has access to these tools during reasoning:

| Tool | Purpose |
|------|---------|
| `search_man_vault` | Query documentation index |
| `search_incidents` | Find similar past incidents |
| `search_capabilities` | Find available capabilities |
| `execute_capability` | Run a capability (policy-enforced) |
| `get_system_info` | Query system state |
| `shell_command` | Safe shell execution (denylist enforced) |

## Intent routing

User input is classified by a rule-based router (no LLM required):

| Intent | Handler |
|--------|---------|
| `system_question` | Agent Loop |
| `system_task` | Agent Loop |
| `shell_passthrough` | Safe subprocess |
| `meta` | Direct handlers |

## Key components and locations

```
src/elle/
  cli/
    agentic/               # THE CORE: Agent loop and tools
      loop.py              # Main agentic execution loop
      tools.py             # Tool definitions for LLM
      incident_recorder.py # Incident recording utilities
      stages.py            # Execution stages
    terminal/              # REPL and rendering
    planner/               # Multi-step plan execution
    fixit/                 # Quick-fix workflows
    setup/                 # First-run setup wizard
  daemon/
    telemetry/             # ALL monitoring lives here
    manvault/              # Documentation index
    incidents/             # Decision memory
    notifications/         # Alert delivery (ntfy, mobile push)
    observability/         # Prometheus/JSON metrics export
    reboot/                # Managed reboot orchestration
    api/                   # FastAPI bridge
  capabilities/
    core/                  # Built-in capabilities
    autogen/               # Package capability generation
    executor.py            # Policy-enforced execution
    registry.py            # Capability registration
  ops/                     # Low-level operations
    editing/               # Tiered config editing
    preflight/             # Command risk classification
  policy/                  # Access control
  reactive/                # Event-driven automations
  mobile/                  # Mobile gateway (mTLS, QR pairing)
  rag/                     # RAG pipeline and confgen
```

## Storage architecture

ELLE uses PostgreSQL for all persistent storage with these logical schemas:

| Schema | Purpose |
|--------|---------|
| Telemetry | Raw events, processed events, fingerprints |
| Incidents | Incident reports, actions, snapshots, decision records |
| Man Vault | Indexed man pages with FTS5 |
| Capabilities | Capability registry, autogen database |
| Reactive | Reactive function definitions and execution history |
| Policy | Access control rules |

## Command modules

These modules ("Arms") have their own slash commands but integrate through The Spine:

| Module | Commands | Records To |
|--------|----------|-----------|
| `/learn` | Package capability generation | `package_learning` arm |
| `/react` | Reactive function management | `reactive_functions` arm |
| `/mobile` | Mobile gateway management | `mobile_gateway` arm |

All arm actions are recorded to the Incident Vault via `record_arm_action()`.

## Self-building architecture

ELLE grows through use:

- **Capabilities grow** via `/learn` package generation — install a package, ELLE learns its operations
- **Incident memory grows** from every interaction — successful solutions inform future decisions
- **Reactive functions** are user-defined automations that execute through the same pipeline
- **Man Vault** expands based on usage patterns
