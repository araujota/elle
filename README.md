# ELLE

**Enabling Layer Learning Everything**

[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-❤-ea4aaa?logo=github)](https://github.com/sponsors/araujota)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

A local-first, agentic system layer for Ubuntu 24.04 LTS that converts kernel-level telemetry into natural language insight and safe system operations.

## Features

### Core Capabilities
- **Interactive Terminal** - Natural language interface to your Ubuntu system
- **Intent Classification** - Automatically understands what you want to do (shell, question, task, fixit)
- **Safe Command Execution** - Dangerous commands are blocked, privileged operations require confirmation
- **Fixit Service** - Automatic diagnosis and repair of failed commands
- **Planner Service** - Multi-step system task planning with verification

### Capabilities & Policy System
- **Typed Capabilities** - Safe, auditable system operations with risk levels and side effects
- **Policy Engine** - Rule-based access control (allow, deny, require_confirmation, require_preview)
- **Reactive Functions** - Event-driven automations from natural language ("when disk > 90%, clean docker")

### Knowledge & Memory
- **Man Vault** - Local SQLite+FTS5 index of ~24,000 man pages with semantic search
- **Incident Vault** - Decision memory storing past incidents, actions, and outcomes for learning

### Configuration Management
- **LLM-Driven Config Generation** - Generate configuration changes from natural language
- **Augeas Integration** - Safe config editing with preview, validation, and rollback
- **File Operations** - Safe file read/write with atomic operations and rollback support
- **Docker Config** - Docker Compose generation, container diagnostics
- **WireGuard Config** - VPN tunnel configuration from natural language
- **Network Diagnosis** - Connectivity testing, firewall explanation, lockdown suggestions

### Capability Auto-Generation
- **Package Learning** - `/learn <package>` generates typed capabilities from installed packages
- **Multi-Source Intelligence** - Extracts from dpkg metadata, shell completions, man pages, systemd units
- **Bootstrap on Install** - Core package capabilities generated automatically after ELLE installation
- **Auto-Learn New Packages** - Automatically generates capabilities when new packages are installed
- **Batch Learning** - `/learn --all` generates capabilities for all installed packages

### GUI Automation (AT-SPI)
- **Application Learning** - `/map <appname>` captures UI structure via accessibility APIs
- **Natural Language Control** - "disable bluetooth in settings" executed as UI actions
- **Self-Healing** - Automatically adapts when UI elements move or change names
- **Recipe Storage** - Versioned UI "recipes" stored locally for reliable automation

### Telemetry & Monitoring
- **Journal/Kernel Watchers** - Real-time system event monitoring
- **Docker Watcher** - Container state change monitoring
- **Inotify Watcher** - File system change monitoring
- **Port Probe** - Network port status monitoring
- **eBPF Probes** - Kernel-level telemetry (OOM, disk I/O, network drops, thermal)
- **Periodic Probes** - SMART, sensors, disk usage, network status
- **Notifications** - ntfy integration for alerts

### System Management
- **Reboot Tracking** - GRUB/kernel management with pre/post verification
- **Polkit Integration** - Secure privileged operations

## Requirements

- Ubuntu 24.04 LTS
- Python 3.10+
- [Ollama](https://ollama.ai) for local LLM inference

## Installation

### 1. Install Ollama

```bash
curl -fsSL https://ollama.ai/install.sh | sh

# Pull the default models (Q8_0 quantization for better efficiency)
ollama pull qwen2.5:7b-instruct-q8_0   # LLM for generation
ollama pull phi3.5:3.8b-mini-instruct-q8_0  # SLM for classification
```

### 2. Install ELLE

```bash
# Clone the repository
git clone https://github.com/araujota/elle.git
cd elle

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install in development mode
pip install -e ".[dev]"
```

### 3. Verify Installation

```bash
# Run tests
pytest

# Start ELLE
elle
```

## Usage

### Interactive Terminal

```bash
$ elle
ELLE - Ubuntu System Assistant
Type 'help' for commands or just start typing.

elle > help
elle > what is using port 8080?
elle > show disk usage
elle > why is nginx failing?
```

### One-shot Commands

```bash
elle status          # Show system status
elle help            # Show help
elle "check disk space"  # Run a query directly
```

### Configuration Generation

ELLE can generate configuration changes from natural language:

```python
from elle.rag.confgen import generate, get_confgen_service

# Generate configuration
outcome = generate(
    request="Set static IP 192.168.1.10 for eth0",
    target_path="/etc/netplan/01-netcfg.yaml",
)

if outcome.success:
    print(outcome.result.explanation)
    for op in outcome.result.operations:
        print(f"  {op.kind} {op.path} = {op.value}")

    # Apply if approved
    service = get_confgen_service()
    service.apply(outcome)
```

Supported configuration domains:
- **network** - Netplan configuration
- **ssh** - SSH daemon settings
- **filesystem** - fstab mount points
- **firewall** - UFW rules
- **service** - Systemd unit files
- **cron** - Scheduled tasks

### Fixit (Command Recovery)

When a command fails, use `fix` to diagnose and repair:

```bash
elle > apt update
# ... command fails ...

elle > fix
Analyzing failure...

Diagnosis: Permission denied - requires sudo
Suggested fix: Run with elevated privileges via Polkit

Apply fix? [y/n]:
```

### Planner (System Tasks)

For complex system changes, ELLE creates a verified plan:

```bash
elle > configure nginx as reverse proxy for localhost:3000

Planning...

Plan: Configure Nginx Reverse Proxy
1. Install nginx (if needed)
2. Create /etc/nginx/sites-available/proxy.conf
3. Enable site with symlink
4. Test configuration (nginx -t)
5. Reload nginx

Risks:
- Port 80 must be available
- Existing nginx config may conflict

Execute plan? [y/n]:
```

### Augeas Config Editing

Safe configuration editing with preview and rollback:

```python
from elle.ops.augeas import AugeasController, AugeasOp

controller = AugeasController()

# Preview changes before applying
ops = [
    AugeasOp(kind="set", path="/files/etc/ssh/sshd_config/PermitRootLogin", value="no"),
]
preview = controller.preview(ops)
print(preview.diff)

# Execute with automatic backup
result = controller.execute(ops)
if not result.success:
    controller.rollback()  # Restore from backup
```

### File Operations

```python
from elle.ops.files import read_file, write_file, MarkdownBuilder

# Read a file
result = read_file("/etc/hostname")
print(result.content)

# Write a file
result = write_file("/tmp/config.txt", "key=value", overwrite=True)

# Build markdown
doc = (
    MarkdownBuilder()
    .heading("Report", level=1)
    .paragraph("Generated by ELLE")
    .code_block("systemctl status nginx", language="bash")
    .build()
)
```

### Telemetry & eBPF

ELLE monitors system health via multiple sources:

```python
from elle.daemon.telemetry import get_recent_events
from elle.daemon.telemetry.ebpf import is_ebpf_available

# Check eBPF availability
if is_ebpf_available():
    print("eBPF probes active: OOM, disk I/O, network drops, thermal")

# Get recent telemetry events
events = get_recent_events(limit=10, severity="warning")
for event in events:
    print(f"{event.ts} [{event.severity}] {event.message}")
```

### Notifications

Configure ntfy for system alerts:

```python
from elle.daemon.notifications import get_notification_service

svc = get_notification_service()
svc.configure(
    ntfy_url="https://ntfy.sh/my-elle-alerts",
    min_severity="warning",
)

# Alerts are sent automatically for:
# - OOM kills
# - Disk space critical (>95%)
# - Service failures
# - SMART warnings
```

### Reactive Functions

Create event-driven automations using natural language:

```bash
elle > /react create "when disk usage exceeds 90%, clean docker images and notify me"

Creating reactive function...

Name: disk-cleanup-alert
Trigger: event (probe/disk)
Condition: {event.raw.used_pct} >= 90
Actions:
  1. docker.prune (remove unused images)
  2. notify.send (alert: "Disk cleanup triggered")
Policy: max_frequency=1h, escalate_on_failure=true

[Approve] [Edit] [Cancel]
```

Manage reactive functions:

```bash
elle > /react list                    # List all functions
elle > /react show disk-cleanup-alert # Show details
elle > /react enable disk-cleanup     # Enable function
elle > /react disable disk-cleanup    # Disable function
elle > /react history disk-cleanup    # View execution history
elle > /react test disk-cleanup       # Dry-run with sample event
```

### Docker Utilities

```bash
# Convert docker run to compose
elle > docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=secret postgres:15
# ELLE suggests: Would you like me to convert this to docker-compose.yml?

# Diagnose container issues
elle > why is my-container restarting?
# Analyzes logs, exit codes, resource limits

# Explain resource usage
elle > show docker resource usage
```

### Network Diagnosis

```bash
# Diagnose connectivity
elle > can't connect to api.example.com:443
# Tests DNS, routing, TLS, firewall rules

# Explain firewall rules
elle > explain ufw rules
# Human-readable firewall summary

# Lockdown suggestions
elle > lock postgres to localhost only
# Suggests ufw rules for service isolation
```

### Package Capability Learning

ELLE can automatically generate typed capabilities from installed packages:

```bash
# Learn a specific package
elle > /learn ffmpeg
Gathering intelligence for 'ffmpeg'...
  Sources: dpkg, bash-completion, man pages
  Extracted: 47 flags, 3 subcommands
Generating capabilities...
  Created: ffmpeg.convert, ffmpeg.probe, ffmpeg.stream
Capabilities saved: 3 (pending approval)

# Learn all installed packages (batch mode)
elle > /learn --all --dry-run
Would learn 127 packages (out of 1,432 total installed)
  - ffmpeg, nginx, docker.io, systemctl...

elle > /learn --all
Learning all 127 packages...
  Complete! 119 succeeded, 8 failed
  Capabilities generated: 342

# List packages with capabilities
elle > /learn list
Packages with generated capabilities:
  ffmpeg     (3 caps, 2 approved)
  nginx      (5 caps, 5 approved)
  docker.io  (12 caps, 10 approved)

# Approve a capability for use
elle > /learn approve ffmpeg.convert
```

**Auto-learning:** When enabled (default), ELLE automatically generates capabilities for newly installed packages. Configure in setup wizard or `~/.config/elle/elle.toml`:

```toml
[daemon]
auto_learn_new_packages = true  # Default: enabled
```

### GUI Automation

Control desktop applications using natural language:

```bash
# Learn an application's UI structure (renamed to /map)
elle > /map gnome-control-center
Learning 'gnome-control-center'...
  Elements: 127
  Navigation targets: 23
  Recipe stored with ID: abc123...

# Execute GUI tasks via natural language
elle > disable bluetooth in settings
Planning GUI task...
  1. Navigate to Bluetooth panel
  2. Click Bluetooth toggle
  3. Verify state is "off"
Execute? [y/n]: y
✓ Bluetooth disabled

# List learned applications
elle > /map list
Learned applications:
  gnome-control-center  v1 (100% conf, 5/5 success)
  nautilus              v2 (95% conf, 18/19 success)

# Show recipe details
elle > /map show gnome-control-center
```

**Note:** GUI automation requires AT-SPI accessibility to be enabled:
```bash
gsettings set org.gnome.desktop.interface toolkit-accessibility true
```

### Reboot Management

Track kernel updates and verify post-reboot:

```python
from elle.daemon.reboot import schedule_reboot, get_pending_reboot

# Check if reboot is needed (e.g., kernel update)
pending = get_pending_reboot()
if pending:
    print(f"Reboot required: {pending.reason}")
    print(f"New kernel: {pending.target_kernel}")

# Schedule with verification
schedule_reboot(
    reason="Kernel update to 6.8.0-40",
    verify_commands=["uname -r", "systemctl is-system-running"],
)
```

## Architecture

```
User ──▶ elle (terminal / CLI)
         ├─▶ Intent Classifier (hybrid: keywords + patterns + SLM)
         ├─▶ Fixit Service (command failure recovery)
         ├─▶ Planner Service (multi-step task planning)
         ├─▶ Man Vault (documentation grounding)
         ├─▶ Incident Vault (decision memory + prior art)
         ├─▶ Config Generator (natural language → config)
         ├─▶ Capabilities (typed operations with policy)
         ├─▶ Capability Auto-Generator (package → capabilities)
         ├─▶ Reactive Engine (event-driven automations)
         ├─▶ Policy Engine (rule-based access control)
         ├─▶ GUI Automation (AT-SPI application control)
         ├─▶ Ollama (local inference)
         └─▶ elled (telemetry + privileged ops)
                ├─▶ Journal/Kernel Watchers
                ├─▶ Docker Watcher (container state)
                ├─▶ Inotify Watcher (file changes)
                ├─▶ Port Probe (network ports)
                ├─▶ Package Probe (new installs + upgrades)
                ├─▶ eBPF Probes (OOM, I/O, network, thermal)
                ├─▶ Periodic Probes (SMART, sensors, df)
                ├─▶ Reactive Scheduler (cron triggers)
                ├─▶ Capability Bootstrap (first-run learning)
                ├─▶ Reboot Manager (GRUB, verification)
                └─▶ Notifications (ntfy)
```

### Request Lifecycle

Every natural language message follows this flow through ELLE's processing pipeline:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         REQUEST LIFECYCLE                                     │
└──────────────────────────────────────────────────────────────────────────────┘

  User Input (CLI or API)
       │
       ▼
┌──────────────────┐
│  Engine.process  │  ◄── Entry point for all requests
│  (engine.py)     │      Receives: input string + Session
└────────┬─────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                        INTENT CLASSIFICATION                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │ 1. Hard keyword routes    (exact matches: "help", "exit", "status")    │ │
│  │ 2. Prefix commands        (/ask, /do, /sh, /fix, /react, /learn, !)    │ │
│  │ 3. Pattern matching       (regex for shell, questions, tasks, GUI)     │ │
│  │ 4. SLM classification     (Ollama phi3.5 fallback if no pattern hit)   │ │
│  │ 5. Safety overrides       (reduce confidence for dangerous commands)   │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                               │
│  Output: ClassificationResult(intent, confidence, reasoning)                  │
│                                                                               │
│  Intent Types:                                                                │
│  ├── shell_passthrough   Safe shell commands                                  │
│  ├── system_question     "Why is nginx failing?" "What uses port 8080?"       │
│  ├── system_task         "Install nginx", "Configure static IP"               │
│  ├── gui_task            "Disable bluetooth in settings"                      │
│  ├── fixit               "fix" after a command failure                        │
│  ├── navigation          "status", "events", "logs"                           │
│  ├── meta                "help", "exit", "config"                             │
│  └── explain_command     "explain: ls -la"                                    │
└──────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                           INTENT ROUTING                                      │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  shell_passthrough ────▶ ┌─────────────────┐                                  │
│                          │ Denylist Check  │ Block: rm -rf /, fork bombs,     │
│                          └────────┬────────┘        sudo, curl|bash, etc.     │
│                                   │                                           │
│                                   ▼                                           │
│                          ┌─────────────────┐                                  │
│                          │ subprocess_run  │ Execute safely, capture output   │
│                          └─────────────────┘                                  │
│                                                                               │
│  system_question ──────▶ ┌─────────────────┐    ┌─────────────────┐           │
│                          │   Man Vault     │───▶│    Ollama LLM   │           │
│                          │   (RAG docs)    │    │   (generation)  │           │
│                          └─────────────────┘    └─────────────────┘           │
│                                                                               │
│  system_task ──────────▶ ┌─────────────────────────────────────────────────┐  │
│                          │              PLANNER SERVICE                     │  │
│                          │  ┌──────────────────────────────────────────┐   │  │
│                          │  │ 1. Query Man Vault for relevant docs     │   │  │
│                          │  │ 2. Query Incident Vault for prior art    │   │  │
│                          │  │ 3. Query daemon for system state         │   │  │
│                          │  │ 4. LLM generates CapabilityPlan          │   │  │
│                          │  │ 5. User confirmation (if required)       │   │  │
│                          │  └──────────────────────────────────────────┘   │  │
│                          │                      │                          │  │
│                          │                      ▼                          │  │
│                          │  ┌──────────────────────────────────────────┐   │  │
│                          │  │         CAPABILITY EXECUTION              │   │  │
│                          │  │  ┌────────────────────────────────────┐  │   │  │
│                          │  │  │ Policy Engine                      │  │   │  │
│                          │  │  │ ├── Check rules (ALLOW/DENY)       │  │   │  │
│                          │  │  │ ├── REQUIRE_CONFIRMATION prompts   │  │   │  │
│                          │  │  │ └── REQUIRE_PREVIEW shows diff     │  │   │  │
│                          │  │  └────────────────────────────────────┘  │   │  │
│                          │  │                   │                       │   │  │
│                          │  │                   ▼                       │   │  │
│                          │  │  ┌────────────────────────────────────┐  │   │  │
│                          │  │  │ Capability Executor                │  │   │  │
│                          │  │  │ ├── Execute capability             │  │   │  │
│                          │  │  │ ├── Collect evidence               │  │   │  │
│                          │  │  │ └── Record to Incident Vault       │  │   │  │
│                          │  │  └────────────────────────────────────┘  │   │  │
│                          │  └──────────────────────────────────────────┘   │  │
│                          └─────────────────────────────────────────────────┘  │
│                                                                               │
│  fixit ────────────────▶ ┌─────────────────────────────────────────────────┐  │
│                          │              FIXIT SERVICE                       │  │
│                          │  1. Analyze exit code, stdout, stderr            │  │
│                          │  2. Search Incident Vault for similar failures   │  │
│                          │  3. Query Man Vault for command docs             │  │
│                          │  4. LLM diagnosis (or rule-based fallback)       │  │
│                          │  5. Generate fix via Capabilities                │  │
│                          └─────────────────────────────────────────────────┘  │
│                                                                               │
│  gui_task ─────────────▶ ┌─────────────────────────────────────────────────┐  │
│                          │             AT-SPI AUTOMATION                    │  │
│                          │  1. Load UI recipe for target application        │  │
│                          │  2. LLM plans UITaskPlan from request            │  │
│                          │  3. Execute UIActions via AT-SPI                 │  │
│                          │  4. Self-heal if elements moved (fuzzy match)    │  │
│                          │  5. Record execution to Incident Vault           │  │
│                          └─────────────────────────────────────────────────┘  │
│                                                                               │
│  navigation/meta ──────▶ Direct handlers (status, help, exit, events, etc.)  │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              RESPONSE                                         │
│                                                                               │
│  EngineResult:                                                                │
│  ├── output        Rendered text/markdown for display                         │
│  ├── session       Updated Session (immutable, new instance)                  │
│  ├── action        CONTINUE | EXIT | CLEAR                                    │
│  └── success       True/False                                                 │
│                                                                               │
│  Side effects:                                                                │
│  ├── Incident created/updated in Incident Vault                               │
│  ├── Telemetry events recorded (if system mutated)                            │
│  └── Reactive functions may trigger from state changes                        │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Confidence Thresholds:**
- `HIGH (0.90)` - Proceed without confirmation
- `MEDIUM (0.75)` - May require confirmation for mutations
- `MIN (0.55)` - Low confidence, user clarification may be needed

### Key Components

| Component | Description |
|-----------|-------------|
| `elle` | Interactive terminal REPL and CLI |
| `elled` | Background daemon for telemetry and privileged ops |
| Intent Classifier | Hybrid classification (keywords, patterns, SLM fallback) |
| Fixit Service | Diagnose and repair failed commands |
| Planner Service | Plan multi-step system tasks with verification |
| Man Vault | SQLite+FTS5 index of ~24,000 man pages |
| Incident Vault | Decision memory storing incidents and outcomes |
| Config Generator | LLM-driven config from natural language |
| Capabilities | Typed system operations with risk levels and side effects |
| Capability Auto-Gen | Generate capabilities from packages via multi-source intelligence |
| Policy Engine | Rule-based access control for capabilities |
| Reactive Engine | Event-driven automation execution |
| Augeas Controller | Safe config editing with preview/rollback |
| GUI Automation | AT-SPI application control with self-healing (`/map` command) |
| Package Probe | Monitor package installs/upgrades, trigger auto-learning |
| eBPF Watcher | Kernel-level telemetry probes |
| Reboot Manager | GRUB/kernel management with verification |

## Development

```bash
# Activate environment
source .venv/bin/activate

# Run tests
pytest

# Run tests with coverage
pytest --cov=elle

# Lint
ruff check src/

# Format
ruff format src/

# Type check
mypy src/
```

### OpenAI-Compatible API

ELLE exposes an OpenAI-compatible API for programmatic access. All requests flow through the same intent classification and policy pipeline as the CLI.

**Important:** This is NOT direct LLM access. Every request is classified, policy-checked, and executed through ELLE's capability system.

#### Authentication

The daemon API uses session token authentication. When `elled` starts, it generates a cryptographic token stored at:

```
$XDG_RUNTIME_DIR/elle/session.token
```

Include this token in requests via header:

```bash
# Read the session token
TOKEN=$(cat $XDG_RUNTIME_DIR/elle/session.token)

# Use X-Elle-Token header
curl -H "X-Elle-Token: $TOKEN" http://localhost:8420/v1/models

# Or use Bearer token format
curl -H "Authorization: Bearer $TOKEN" http://localhost:8420/v1/models
```

#### Available Models (Execution Modes)

| Model ID | Description |
|----------|-------------|
| `elle` | Full execution mode - can perform all operations (with policy enforcement) |
| `elle.readonly` | Read-only mode - queries and explanations only, no system mutations |
| `elle.capabilities_only` | Returns planned capabilities as `tool_calls` without executing them |

#### Endpoints

**List Models**
```bash
GET /v1/models

# Response
{
  "object": "list",
  "data": [
    {"id": "elle", "object": "model", "owned_by": "elle"},
    {"id": "elle.readonly", "object": "model", "owned_by": "elle"},
    {"id": "elle.capabilities_only", "object": "model", "owned_by": "elle"}
  ]
}
```

**Chat Completions**
```bash
POST /v1/chat/completions

# Request body
{
  "model": "elle",
  "messages": [
    {"role": "user", "content": "What is using port 8080?"}
  ],
  "stream": false
}

# Response
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "elle",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Port 8080 is being used by..."
    },
    "finish_reason": "stop"
  }],
  "usage": {...}
}
```

#### Python Example

```python
import os
from pathlib import Path
from openai import OpenAI

# Read session token
runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
token_path = Path(runtime_dir) / "elle" / "session.token"
token = token_path.read_text().strip()

# Create client pointing to elled
client = OpenAI(
    base_url="http://localhost:8420/v1",
    api_key=token,  # Token goes here
)

# Query ELLE
response = client.chat.completions.create(
    model="elle.readonly",  # Read-only for safety
    messages=[
        {"role": "user", "content": "Show disk usage"}
    ]
)
print(response.choices[0].message.content)

# Execute a task (requires "elle" model)
response = client.chat.completions.create(
    model="elle",
    messages=[
        {"role": "user", "content": "Clean docker images older than 7 days"}
    ]
)
# Will go through policy checks, may require confirmation
```

#### Streaming

```python
response = client.chat.completions.create(
    model="elle",
    messages=[{"role": "user", "content": "Explain nginx config"}],
    stream=True
)

for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

#### curl Examples

```bash
TOKEN=$(cat $XDG_RUNTIME_DIR/elle/session.token)

# Simple query (readonly mode)
curl -X POST http://localhost:8420/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Elle-Token: $TOKEN" \
  -d '{
    "model": "elle.readonly",
    "messages": [{"role": "user", "content": "What services are running?"}]
  }'

# Execute a task (full mode)
curl -X POST http://localhost:8420/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Elle-Token: $TOKEN" \
  -d '{
    "model": "elle",
    "messages": [{"role": "user", "content": "Restart nginx"}]
  }'

# Get capabilities without execution
curl -X POST http://localhost:8420/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Elle-Token: $TOKEN" \
  -d '{
    "model": "elle.capabilities_only",
    "messages": [{"role": "user", "content": "Install nginx as reverse proxy"}]
  }'
# Returns tool_calls with planned capabilities
```

#### Security Notes

- Session tokens are ephemeral - regenerated each time `elled` starts
- Token file has 600 permissions (owner read/write only)
- Tokens use constant-time comparison to prevent timing attacks
- All requests go through the same policy engine as CLI commands
- High-risk operations still require confirmation via the CLI

## Configuration

ELLE stores data in:
- `~/.local/state/elle/` - User state and logs
- `/var/lib/elle/` - System databases (Man Vault, Incident Vault, telemetry)
- `/var/lib/elle/backups/` - Configuration backups

## Safety

ELLE includes multiple safety mechanisms:
- **Command denylist** - Blocks dangerous commands (rm -rf /, fork bombs, etc.)
- **No implicit sudo** - Privileged operations require explicit confirmation
- **Preview before apply** - Configuration changes show diffs before execution
- **Automatic backups** - Critical configs are backed up before modification
- **Rollback support** - Failed operations can be reverted

## Support ELLE

ELLE is open source and free to use. If you find it valuable, consider sponsoring development:

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor_on_GitHub-❤-ea4aaa?style=for-the-badge&logo=github)](https://github.com/sponsors/araujota)

**Other ways to help:**
- Report bugs and suggest features on [GitHub Issues](https://github.com/araujota/elle/issues)
- Contribute code or documentation
- Share ELLE with others who might find it useful

**Questions or feedback?** Email: araujota97@gmail.com

## License

GPL-3.0-or-later

## Links

- **Website:** https://araujota.github.io/elle
- **Repository:** https://github.com/araujota/elle
- **Sponsor:** https://github.com/sponsors/araujota
