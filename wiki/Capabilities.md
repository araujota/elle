# Capabilities

Capabilities are typed, policy-governed, auditable operations — the fundamental units of system mutation in ELLE. Every system-modifying action goes through the Capability system.

## What is a capability?

A capability is a Pydantic-modeled operation with:

- **Explicit input/output schemas** — typed parameters and return values
- **Risk level** — `none`, `low`, `medium`, `high`, `critical`
- **Side effects** — declared upfront (e.g., "modifies file", "restarts service")
- **Trust level** — `core` (built-in), `official`, `third_party`
- **Domain** — category grouping (service, file, network, etc.)

```python
class CapabilitySpec(BaseModel):
    name: str                    # e.g., "service.restart"
    domain: str                  # service, file, network, etc.
    risk: str                    # none, low, medium, high, critical
    side_effects: tuple[SideEffect, ...]
    trust_level: str             # core, official, third_party
```

## Execution flow

```
Agent Loop → CapabilityExecutor → PolicyEngine → Capability.run() → Evidence → Incident
```

1. The Agent Loop selects a capability based on LLM reasoning
2. The `CapabilityExecutor` validates inputs and checks policy
3. The `PolicyEngine` evaluates rules: allow, deny, or require_confirmation
4. If allowed, `Capability.run()` executes the operation
5. Evidence (stdout, stderr, exit code, duration) is captured
6. The result is recorded to the Incident Vault

## Core capability domains

### Service

| Capability | Risk | Description |
|-----------|------|-------------|
| `service.start` | low | Start a systemd service |
| `service.stop` | medium | Stop a systemd service |
| `service.restart` | medium | Restart a systemd service |
| `service.status` | none | Check service status |

### File

| Capability | Risk | Description |
|-----------|------|-------------|
| `file.read` | none | Read file contents |
| `file.write` | medium | Write to a file |
| `file.delete` | high | Delete a file |
| `file.copy` | low | Copy a file |
| `file.diff` | none | Show file differences |

### Config

| Capability | Risk | Description |
|-----------|------|-------------|
| `config.edit` | medium | Edit a configuration file (tiered: Augeas, crudini, yq, xmlstarlet) |
| `config.preview` | none | Preview config changes as a diff |
| `config.validate` | none | Validate configuration syntax |
| `config.rollback` | medium | Restore config from incident-linked backup |

### Network

| Capability | Risk | Description |
|-----------|------|-------------|
| `network.diagnose` | none | Run network diagnostics |
| `network.listeners` | none | List listening ports |
| `wireguard.generate-key` | low | Generate WireGuard keypair |
| `wireguard.rotate-keys` | medium | Rotate WireGuard keys |

### Docker

| Capability | Risk | Description |
|-----------|------|-------------|
| `docker.list` | none | List containers |
| `docker.inspect` | none | Inspect a container |
| `docker.stop` | medium | Stop a container |
| `docker.prune` | medium | Clean unused resources |

### Package

| Capability | Risk | Description |
|-----------|------|-------------|
| `package.install` | high | Install a package via apt |
| `package.remove` | high | Remove a package |
| `package.info` | none | Query package information |

### Auth

| Capability | Risk | Description |
|-----------|------|-------------|
| `auth.session_token` | low | Generate session token |
| `auth.mobile_certs` | medium | Manage mobile gateway certificates |

## Auto-generation via `/learn`

ELLE can generate capabilities for installed packages:

```
elle> /learn ffmpeg
Generating capabilities: ffmpeg.convert, ffmpeg.probe, ffmpeg.stream

elle> /learn --all
Generating capabilities for 1,247 installed packages...

elle> /learn approve ffmpeg.convert
```

The `/learn` system extracts capability definitions from multiple sources:
- `dpkg` package metadata
- Shell completions
- Man pages
- Systemd unit files

### Auto-learn

When `auto_learn_new_packages` is enabled (default), ELLE automatically generates capabilities when new packages are installed. The daemon monitors `/var/lib/dpkg/status` for changes.

## Policy engine

The Policy Engine evaluates rules for each capability execution:

| Action | Description |
|--------|-------------|
| `allow` | Execute without prompting |
| `deny` | Block execution |
| `require_confirmation` | Ask user before executing |

Rules can match on:
- Capability name or domain
- Risk level
- Trust level
- Time of day
- User identity

The safety level configured in the [[Setup Wizard|Setup-Wizard]] translates to a default policy:

| Safety Level | Behavior |
|-------------|----------|
| Standard | Blocks dangerous commands, confirms high-risk operations |
| Cautious | Confirms most changes, previews all config edits |
| Minimal | Only blocks the most dangerous patterns |

## Risk levels

| Level | When to use | Example |
|-------|------------|---------|
| `none` | Read-only, no side effects | `service.status` |
| `low` | Minor changes, easily reversible | `file.copy` |
| `medium` | Service-affecting, usually reversible | `service.restart` |
| `high` | Data-modifying, hard to reverse | `package.install` |
| `critical` | Potentially destructive | `file.delete /etc/...` |
