# ELLE

**Enabling Layer Learning Everything**

A local-first, agentic system layer for Ubuntu 24.04 LTS that converts kernel-level telemetry into natural language insight and safe system operations.

## Features

### Core Capabilities
- **Interactive Terminal** - Natural language interface to your Ubuntu system
- **Intent Classification** - Automatically understands what you want to do (shell, question, task, fixit)
- **Safe Command Execution** - Dangerous commands are blocked, privileged operations require confirmation
- **Fixit Service** - Automatic diagnosis and repair of failed commands
- **Planner Service** - Multi-step system task planning with verification

### Knowledge & Memory
- **Man Vault** - Local SQLite+FTS5 index of ~24,000 man pages with semantic search
- **Incident Vault** - Decision memory storing past incidents, actions, and outcomes for learning

### Configuration Management
- **LLM-Driven Config Generation** - Generate configuration changes from natural language
- **Augeas Integration** - Safe config editing with preview, validation, and rollback
- **File Operations** - Safe file read/write with atomic operations and rollback support

### Telemetry & Monitoring
- **Journal/Kernel Watchers** - Real-time system event monitoring
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

# Pull the default model
ollama pull qwen2.5:7b-instruct
```

### 2. Install ELLE

```bash
# Clone the repository
git clone https://github.com/your-username/elle.git
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
         ├─▶ Ollama (local inference)
         └─▶ elled (telemetry + privileged ops)
                ├─▶ Journal/Kernel Watchers
                ├─▶ eBPF Probes (OOM, I/O, network, thermal)
                ├─▶ Periodic Probes (SMART, sensors, df)
                ├─▶ Reboot Manager (GRUB, verification)
                └─▶ Notifications (ntfy)
```

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
| Augeas Controller | Safe config editing with preview/rollback |
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

## License

GPL-3.0-or-later
