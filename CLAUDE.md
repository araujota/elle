# CLAUDE.md

## Project Overview

ELLE (Enabling Layer Learning Everything) is a local-first, agentic system layer for Ubuntu 24.04 LTS that converts kernel-level telemetry into natural language insight and safe system operations.

## The Spine: Core Architecture

**ALL functionality MUST flow through this pipeline:**

```
DAEMON → SIGNALS → INCIDENT REPORT → AGENT LOOP → CAPABILITIES → OUTCOME → INCIDENT MEMORY
```

If functionality doesn't flow through The Spine, it must be refactored or removed.

## The Three Pillars

ELLE's architecture rests on three foundational pillars. **These are non-negotiable.**

### 1. The Daemon Owns ALL Passive Monitoring

The daemon (`elled`) is the **sole owner** of all passive system observation.

- **Telemetry ingestion**: Journal, kernel, eBPF, inotify, probes - ALL flow through the daemon
- **Event correlation**: Normalizes, deduplicates, and fingerprints events
- **State tracking**: System state changes detected and recorded by the daemon
- **No CLI polling**: The CLI never polls - it queries the daemon or reacts to daemon events

**Why:** Centralized observation enables consistent handling, prevents race conditions, and ensures complete audit trails.

### 2. Capabilities Are the ONLY Way to Mutate

Capabilities are typed, policy-governed, auditable operations - the fundamental units of system mutation.

**Capabilities MUST:**
- Have explicit input/output schemas (Pydantic models)
- Declare risk level and side effects
- Be governed by the Policy Engine
- Produce evidence for incident records
- Be executed via `CapabilityExecutor`

**The Agent Loop plans with Capabilities**, not shell commands. All mutations are recorded to the Incident Vault with full provenance.

### 3. The Agent Loop Orchestrates Everything

The Agent Loop is the LLM-powered reasoning engine that:
1. Creates an Incident Report for provenance
2. Retrieves context from Man Vault + Incident Vault
3. Reasons over the context with the LLM
4. Executes Capabilities (policy-enforced)
5. Records outcome to Incident Memory

**Location:** `cli/agentic/loop.py`

## Architectural Mandate

### New Functionality MUST Emerge from Core Systems

When new functionality is requested, it **must** come through one of these channels:

| Channel | Examples |
|---------|----------|
| **Daemon upgrades** | New watchers, probes, eBPF programs |
| **New Capabilities** | Typed operations added to `capabilities/core/` |
| **Agent Loop improvements** | Better retrieval, reasoning, or tool integration |
| **Incident models** | Extended provenance, evidence types |

### Hardcoded Flows Are Forbidden

Never build:
- Direct subprocess calls that bypass capabilities
- Diagnostic logic with hardcoded patterns instead of LLM reasoning
- File operations that don't go through `file.*` capabilities
- Domain-specific modules that don't integrate with The Spine

### Incident Recording Is Mandatory

Every significant action MUST create or update an incident record:
- Capability executions → recorded automatically by executor
- Command module actions → use `record_arm_action()` from `incident_recorder.py`
- Background operations → create incident with provenance

## Repository Structure

```
src/elle/
  cli/
    agentic/               # THE CORE: Agent loop and tools
      loop.py              # Main agentic execution loop
      tools.py             # Tool definitions for LLM
      incident_recorder.py # Incident recording utilities
      stages.py            # Execution stages
    terminal/              # REPL and rendering
    package_learn_commands.py  # /learn (records to incident vault)
    map_commands.py        # /map (records to incident vault)
    reactive_commands.py   # /react (records to incident vault)
    mobile_commands.py     # /mobile (records to incident vault)
  daemon/
    telemetry/             # ALL monitoring lives here
    manvault/              # Documentation index
    incidents/             # Decision memory (THE source of truth)
    api/                   # FastAPI bridge
  capabilities/
    core/                  # Built-in capabilities
    autogen/               # Package capability generation
    executor.py            # Policy-enforced execution
    registry.py            # Capability registration
  policy/                  # Access control
  reactive/                # Event-driven automations
  mobile/                  # Mobile gateway
tests/
```

## Development Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                    # Run tests
ruff check src/           # Lint
ruff format src/          # Format
mypy src/                 # Type check
```

## Core Principles

- **Local-first**: All inference via Ollama
- **No ambient sudo**: Privileged ops are Polkit-gated
- **Everything through The Spine**: No bypassing the pipeline
- **Incident memory**: Every action recorded with provenance
- **Pydantic everywhere**: All boundaries use typed models

## Security Constraints

**Subprocess denylist** (blocked even in passthrough):
- `rm -rf /`, fork bombs, `sudo`, `curl | bash`, `mkfs`, `dd of=/dev`

**Privileged operations**: Must go through Polkit helper in daemon

## Key Models

```python
class CapabilitySpec(BaseModel):
    name: str                    # e.g., "service.restart"
    domain: str                  # service, file, network, etc.
    risk: str                    # none, low, medium, high, critical
    side_effects: tuple[SideEffect, ...]
    trust_level: str             # core, official, third_party

class IncidentReport(BaseModel):
    incident_id: str
    domain: str
    severity: str
    status: str                  # open, mitigated, resolved
    outcome: str                 # improved, partial, no_change, worse
    decision_record: DecisionRecord  # Full provenance

class DecisionRecord(BaseModel):
    chosen_approach: str
    rationale: str
    confidence: ConfidenceBreakdown
    provenance: Provenance       # Citations from Man/Incident Vault
```

## Storage Locations

- Event DB: `/var/lib/elle/elle.db`
- Man Vault: `/var/lib/elle/manvault.db`
- Incident Vault: `/var/lib/elle/incidents.db`
- Capabilities: `/var/lib/elle/autogen.db`
- Reactive Functions: `/var/lib/elle/reactive.db`
- Policy Rules: `/var/lib/elle/policy.db`
- UI Recipes: `/var/lib/elle/recipes.db`

## Capabilities

All system mutations go through capabilities:

| Domain | Capabilities |
|--------|-------------|
| service | start, stop, restart, status |
| file | read, write, delete, copy, diff |
| config | edit, preview, validate |
| network | diagnose, listeners, wireguard.* |
| docker | list, inspect, stop, prune |
| package | install, remove, info |
| auth | session_token, mobile_certs |
| gui | click, type, navigate, learn |

**Execution flow:**
```
Agent Loop → CapabilityExecutor → PolicyEngine → Capability.run() → Evidence → Incident
```

## Agent Loop Tools

The agent loop has access to these tools (defined in `tools.py`):

| Tool | Purpose |
|------|---------|
| `search_man_vault` | Query documentation |
| `search_incidents` | Find similar past incidents |
| `search_capabilities` | Find available capabilities |
| `execute_capability` | Run a capability (policy-enforced) |
| `get_system_info` | Query system state |
| `shell_command` | Safe shell execution (denylist enforced) |

## Command Modules ("Arms")

These modules have their own commands but MUST:
1. Record all actions to the Incident Vault via `record_arm_action()`
2. Use capabilities for system mutations
3. Never bypass The Spine

| Module | Commands | Records To |
|--------|----------|-----------|
| `/learn` | package capability generation | `package_learning` arm |
| `/map` | GUI automation learning | `gui_mapping` arm |
| `/react` | reactive function management | `reactive_functions` arm |
| `/mobile` | mobile gateway management | `mobile_gateway` arm |

## LLM Configuration

**SLM (Classification):** `phi3.5:3.8b-mini-instruct-q8_0`
**LLM (Generation):** `qwen2.5:7b-instruct-q8_0`

## Intent Classification

Inputs are classified before routing:

| Intent | Handler |
|--------|---------|
| `system_question` | Agent Loop |
| `system_task` | Agent Loop |
| `gui_task` | Agent Loop (GUI tools) |
| `shell_passthrough` | Safe subprocess |
| `meta` | Direct handlers |

## Self-Building Architecture

ELLE builds itself through use:
- **Capabilities grow** via `/learn` package generation
- **Incident memory grows** from every interaction
- **Reactive functions** are user-defined automations
- **Man Vault** expands based on usage patterns

**Anti-patterns:**
- Building hardcoded handlers
- Creating domain modules that don't use capabilities
- Adding features outside The Spine
