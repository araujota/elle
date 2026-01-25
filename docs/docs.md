---
layout: default
title: Documentation
---

# Documentation

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

## Safety Model

ELLE follows a strict safety model to prevent accidental damage.

### Explain → Plan → Confirm → Apply

Every system-modifying task follows this workflow:

1. **Explain** — ELLE describes what it will do and why
2. **Plan** — Shows the exact commands to run
3. **Confirm** — Asks for your approval
4. **Apply** — Executes with rollback capability

### Blocked Commands

Some commands are always blocked:

- `rm -rf /` and destructive rm patterns
- Fork bombs
- `mkfs` and disk formatting
- `dd` to block devices
- `curl | bash` pipe-to-shell patterns
- `sudo` (use Polkit authentication instead)

### Privilege Escalation

ELLE never uses `sudo`. Privileged operations go through Polkit, which:

- Prompts for your password
- Logs all privileged actions
- Can be configured per-action

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

[daemon.api]
enabled = true               # REST API
host = "127.0.0.1"          # Bind address
port = 8377                  # Port number
```

### Safety Levels

Configure via `/reconfigure` or edit `~/.config/elle/policy.yaml`:

- **Standard** — Blocks dangerous commands, confirms high-risk operations
- **Cautious** — Maximum protection, confirms most changes
- **Minimal** — Only blocks the most dangerous patterns

## Architecture

```
User ──▶ elle (CLI/REPL)
         ├─▶ Man Vault (documentation search)
         ├─▶ Incident Vault (decision memory)
         ├─▶ Ollama (local AI inference)
         └─▶ elled (daemon)
               ├─▶ Telemetry (journal, kernel, probes)
               ├─▶ REST API
               └─▶ Polkit helper
```

### Components

| Component | Description |
|-----------|-------------|
| `elle` | CLI and REPL interface |
| `elled` | Background daemon for telemetry and API |
| Man Vault | SQLite+FTS5 index of man pages |
| Incident Vault | Decision memory database |

## API

ELLE provides an OpenAI-compatible REST API on port 8377.

### Endpoints

```bash
# Chat completion
curl http://localhost:8377/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "elle", "messages": [{"role": "user", "content": "hello"}]}'

# Health check
curl http://localhost:8377/health

# List models
curl http://localhost:8377/v1/models
```

### Streaming

The API supports Server-Sent Events for streaming responses:

```bash
curl http://localhost:8377/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "elle", "messages": [...], "stream": true}'
```

## Reactive Functions

Automate responses to system events with reactive functions.

```
elle> /react create
What should trigger this automation?
> when disk usage exceeds 90%

What should happen?
> send a notification and clean apt cache
```

### Managing Reactive Functions

```
/react list              # List all functions
/react show <id>         # Show details
/react enable <id>       # Enable function
/react disable <id>      # Disable function
/react delete <id>       # Delete function
/react history           # Execution history
```

## GUI Automation

ELLE can control desktop applications via AT-SPI accessibility APIs.

```
elle> /learn firefox     # Learn Firefox's UI
elle> open firefox and go to github.com
```

Requires a graphical session and accessibility to be enabled in your desktop environment.

## Further Reading

- [Hardware Requirements](hardware)
- [GitHub Repository](https://github.com/araujota/elle)
