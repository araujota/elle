---
layout: default
title: Documentation
---

# Documentation

Complete reference for ELLE commands, configuration, and features.

---

## Getting Started

After installation, launch ELLE by typing `elle` in your terminal. On first run, a setup wizard guides you through configuration.

### Basic Usage

ELLE understands natural language. Just type what you want:

```
elle> how much disk space is left?
elle> what services are running?
elle> show me recent errors in the logs
```

### Command Prefixes

For explicit control, use command prefixes:

| Prefix | Purpose | Example |
|--------|---------|---------|
| `/ask` | Force as question | `/ask what does cron.allow do?` |
| `/do` | Force as task | `/do restart nginx` |
| `/sh` | Shell passthrough | `/sh df -h` |
| `/fix` | Diagnose last failure | `/fix` |
| `!` | Shell passthrough (shortcut) | `!ls -la` |

### Slash Commands

| Command | Description |
|---------|-------------|
| `/help` | Show help |
| `/status` | System status overview |
| `/events` | Recent telemetry events |
| `/incidents` | Open incidents |
| `/history` | Command history |
| `/reconfigure` | Re-run setup wizard |
| `/clear` | Clear screen |
| `/exit` | Exit ELLE |
| `/react` | Manage reactive functions |
| `/learn` | GUI automation commands |

---

## Safety Model

ELLE follows a strict safety model to prevent accidental damage.

### Explain → Plan → Confirm → Apply

Every system-modifying task follows this workflow:

<div class="row">
  <div class="col s12 m6 l3">
    <div class="feature-card" style="text-align: center;">
      <i class="material-icons feature-icon">lightbulb</i>
      <h4>1. Explain</h4>
      <p>ELLE describes what it will do and why</p>
    </div>
  </div>
  <div class="col s12 m6 l3">
    <div class="feature-card" style="text-align: center;">
      <i class="material-icons feature-icon">list_alt</i>
      <h4>2. Plan</h4>
      <p>Shows the exact capabilities to execute</p>
    </div>
  </div>
  <div class="col s12 m6 l3">
    <div class="feature-card" style="text-align: center;">
      <i class="material-icons feature-icon">check_circle</i>
      <h4>3. Confirm</h4>
      <p>Asks for your approval</p>
    </div>
  </div>
  <div class="col s12 m6 l3">
    <div class="feature-card" style="text-align: center;">
      <i class="material-icons feature-icon">play_arrow</i>
      <h4>4. Apply</h4>
      <p>Executes with rollback capability</p>
    </div>
  </div>
</div>

### Blocked Commands

Some commands are always blocked:

| Pattern | Reason |
|---------|--------|
| `rm -rf /` | Destructive rm patterns |
| `:(){ :\|:& };:` | Fork bombs |
| `mkfs`, `mkswap` | Disk formatting |
| `dd of=/dev/sda` | Raw disk writes |
| `curl \| bash` | Pipe-to-shell patterns |
| `sudo` | Use Polkit instead |

### Privilege Escalation

ELLE never uses `sudo`. Privileged operations go through Polkit, which:

- Prompts for your password (with 5-minute caching)
- Logs all privileged actions
- Can be configured per-action

---

## Reactive Functions

<div class="callout callout-success">
<strong>Event-Driven Automation</strong><br>
Create automations that respond to system events in natural language.
</div>

Reactive functions allow ELLE to respond automatically to system events.

### Creating Reactive Functions

```
elle> /react create
What should trigger this automation?
> when disk usage exceeds 90%

What should happen?
> clean apt cache and docker images, then notify me

✓ Created reactive function: disk-cleanup-90
```

### Trigger Types

| Type | Description | Example |
|------|-------------|---------|
| **Event** | Respond to telemetry events | "when a container dies" |
| **Schedule** | Cron-based execution | "every Sunday at 3am" |
| **Manual** | On-demand execution | "when I say 'backup'" |

### Managing Reactive Functions

| Command | Description |
|---------|-------------|
| `/react list` | List all functions |
| `/react show <id>` | Show function details |
| `/react enable <id>` | Enable a function |
| `/react disable <id>` | Disable a function |
| `/react delete <id>` | Delete a function |
| `/react history` | Execution history |
| `/react test <id>` | Test with mock event |

### Example Functions

**Disk Alert:**
```
elle> /react create
> when disk usage exceeds 85%
> send a notification and clean old journal logs
```

**Docker Crash Response:**
```
elle> /react create
> when a container crashes unexpectedly
> diagnose the container and create an incident report
```

**Scheduled Maintenance:**
```
elle> /react create
> every day at 4am
> clean apt cache and rotate logs
```

### Function Policies

Each reactive function has policies that control execution:

| Policy | Description |
|--------|-------------|
| `max_frequency` | Minimum time between executions (e.g., "1m") |
| `max_daily_executions` | Limit per day |
| `require_confirmation` | Ask before executing |
| `allowed_hours` | Only run during certain hours |

---

## Capabilities System

Every system operation in ELLE is a **Capability** — a typed, policy-governed unit of work.

### Core Capabilities

<div class="row">
  <div class="col s12 m6">
    <div class="card">
      <div class="card-content">
        <span class="card-title">Service Management</span>
        <ul>
          <li><code>service.restart</code> — Restart services</li>
          <li><code>service.stop</code> — Stop services</li>
          <li><code>service.enable</code> — Enable at boot</li>
        </ul>
      </div>
    </div>
  </div>
  <div class="col s12 m6">
    <div class="card">
      <div class="card-content">
        <span class="card-title">Package Management</span>
        <ul>
          <li><code>package.install</code> — Install packages</li>
          <li><code>package.remove</code> — Remove packages</li>
          <li><code>package.update</code> — Update packages</li>
        </ul>
      </div>
    </div>
  </div>
</div>

<div class="row">
  <div class="col s12 m6">
    <div class="card">
      <div class="card-content">
        <span class="card-title">Docker Operations</span>
        <ul>
          <li><code>docker.prune</code> — Clean resources</li>
          <li><code>docker.stop</code> — Stop containers</li>
          <li><code>docker.diagnose</code> — Analyze crashes</li>
          <li><code>docker.rollback</code> — Revert image</li>
        </ul>
      </div>
    </div>
  </div>
  <div class="col s12 m6">
    <div class="card">
      <div class="card-content">
        <span class="card-title">Configuration</span>
        <ul>
          <li><code>config.edit</code> — Edit files safely</li>
          <li><code>config.backup</code> — Create backups</li>
          <li><code>config.restore</code> — Restore from backup</li>
        </ul>
      </div>
    </div>
  </div>
</div>

<div class="row">
  <div class="col s12 m6">
    <div class="card">
      <div class="card-content">
        <span class="card-title">Network / WireGuard</span>
        <ul>
          <li><code>wireguard.generate-key</code> — Generate keys</li>
          <li><code>wireguard.rotate-keys</code> — Rotate keys</li>
          <li><code>network.listeners</code> — List ports</li>
        </ul>
      </div>
    </div>
  </div>
  <div class="col s12 m6">
    <div class="card">
      <div class="card-content">
        <span class="card-title">GUI Automation</span>
        <ul>
          <li><code>gui.learn</code> — Learn app UI</li>
          <li><code>gui.click</code> — Click elements</li>
          <li><code>gui.type</code> — Type text</li>
        </ul>
      </div>
    </div>
  </div>
</div>

### Capability Properties

Each capability declares:

| Property | Description |
|----------|-------------|
| **Risk Level** | none, low, medium, high, critical |
| **Side Effects** | What changes it makes |
| **Domain** | Category (service, docker, config, etc.) |
| **Requires Privilege** | Needs Polkit authentication |
| **Idempotent** | Safe to run multiple times |

---

## Configuration

### Config Files

| File | Purpose |
|------|---------|
| `/etc/elle/elle.toml` | System-wide configuration |
| `~/.config/elle/elle.toml` | User overrides |
| `~/.config/elle/policy.yaml` | User policy rules |

### Key Settings

```toml
[daemon]
log_level = "INFO"           # DEBUG, INFO, WARNING, ERROR
journal_enabled = true       # Monitor systemd journal
kernel_enabled = true        # Monitor kernel messages
probes_enabled = true        # Periodic system checks
docker_enabled = true        # Monitor Docker events
ebpf_enabled = false         # eBPF tracing (advanced)

[daemon.api]
enabled = true               # REST API
host = "127.0.0.1"          # Bind address
port = 8377                  # Port number
require_auth = true          # Require session token
```

### Safety Levels

Configure via `/reconfigure` or edit `~/.config/elle/policy.yaml`:

| Level | Description |
|-------|-------------|
| **Standard** | Blocks dangerous commands, confirms high-risk operations |
| **Cautious** | Maximum protection, confirms most changes |
| **Minimal** | Only blocks the most dangerous patterns |

### Privilege Levels

| Level | Description |
|-------|-------------|
| **Secure** | Always require password (default) |
| **Convenient** | 'elle' group members skip password |
| **Passwordless** | No authentication required |

---

## Architecture

```
User ──▶ elle (CLI/REPL)
         ├─▶ Man Vault (documentation search)
         ├─▶ Incident Vault (decision memory)
         ├─▶ Ollama (local AI inference)
         └─▶ elled (daemon)
               ├─▶ Telemetry Watchers
               │     ├─▶ Journal Watcher
               │     ├─▶ Kernel Watcher
               │     ├─▶ Docker Watcher
               │     ├─▶ Network Probes
               │     └─▶ eBPF Programs
               ├─▶ Reactive Engine
               ├─▶ REST API
               └─▶ Polkit Helper
```

### Components

| Component | Description |
|-----------|-------------|
| **elle** | CLI and REPL interface |
| **elled** | Background daemon for telemetry and API |
| **Man Vault** | SQLite+FTS5 index of man pages for grounding |
| **Incident Vault** | Decision memory database |
| **Reactive Engine** | Matches events to functions |
| **Capability Executor** | Runs capabilities with policy enforcement |

---

## REST API

ELLE provides an OpenAI-compatible REST API on port 8377.

<div class="callout callout-info">
<strong>Session Tokens</strong><br>
When <code>require_auth</code> is enabled, API requests need a session token header.
</div>

### Endpoints

```bash
# Chat completion
curl http://localhost:8377/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Elle-Session: your-token" \
  -d '{"model": "elle", "messages": [{"role": "user", "content": "hello"}]}'

# Health check
curl http://localhost:8377/health

# List models
curl http://localhost:8377/v1/models

# System state
curl http://localhost:8377/v1/state/docker \
  -H "X-Elle-Session: your-token"
```

### Streaming

The API supports Server-Sent Events for streaming responses:

```bash
curl http://localhost:8377/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "elle", "messages": [...], "stream": true}'
```

---

## Incident Memory

The Incident Vault stores ELLE's decision memory.

### What Gets Recorded

- **Events:** The error or situation that occurred
- **Context:** System state at the time
- **Decision:** What action was taken
- **Outcome:** Whether it worked
- **Fingerprint:** For matching similar incidents

### Searching Incidents

```
elle> /incidents
Open incidents:
  INC-001  nginx-crash  2024-01-15  investigating
  INC-002  disk-full    2024-01-14  resolved

elle> /incidents show INC-001
```

### Learning from History

When ELLE encounters a situation similar to a past incident, it:

1. Searches the Incident Vault by fingerprint
2. Retrieves prior decisions and outcomes
3. Suggests proven solutions first
4. Learns from new outcomes

---

## Further Reading

- [Hardware Requirements]({{ '/docs/hardware' | relative_url }}) — System specifications
- [GitHub Repository]({{ site.github_repo }}) — Source code and issues
- [Discussions]({{ site.github_repo }}/discussions) — Community help

<div class="card">
  <div class="card-content">
    <span class="card-title"><i class="material-icons left" style="color: var(--sponsor-color);">favorite</i>Support ELLE</span>
    <p>If ELLE is useful to you, consider sponsoring development.</p>
  </div>
  <div class="card-action">
    <a href="{{ site.github_sponsor }}" target="_blank"><i class="material-icons left">favorite</i>Sponsor on GitHub</a>
  </div>
</div>
