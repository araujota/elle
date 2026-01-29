# ELLE

**Enabling Layer Learning Everything**

[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-❤-ea4aaa?logo=github)](https://github.com/sponsors/araujota)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

A local-first, agentic system layer for Ubuntu 24.04 LTS that converts kernel-level telemetry into natural language insight and safe system operations.

## Architecture: The Spine

ELLE is built around a unified execution pipeline called **The Spine**:

```
DAEMON → SIGNALS → INCIDENT REPORT → AGENT LOOP → CAPABILITIES → OUTCOME → INCIDENT MEMORY
```

Every interaction flows through this pipeline, ensuring complete auditability, learning from experience, and consistent behavior.

### The Three Pillars

| Pillar | Role |
|--------|------|
| **Daemon (`elled`)** | Owns ALL passive monitoring: telemetry, events, state changes, probes |
| **Capabilities** | Typed, policy-governed operations - the ONLY way to mutate the system |
| **Agent Loop** | LLM-powered reasoning that plans and executes via capabilities |

```
User ──▶ elle (CLI/REPL)
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                     AGENT LOOP                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ 1. Create Incident Report (provenance tracking)          │  │
│  │ 2. Search Man Vault (documentation)                      │  │
│  │ 3. Search Incident Vault (prior decisions)               │  │
│  │ 4. LLM reasons over context                              │  │
│  │ 5. Execute Capabilities (policy-enforced)                │  │
│  │ 6. Record outcome to Incident Memory                     │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                      DAEMON (elled)                              │
│  ├─▶ Telemetry: Journal, Kernel, eBPF, inotify, probes          │
│  ├─▶ Man Vault: ~24,000 man pages indexed                       │
│  ├─▶ Incident Vault: Decision memory + outcomes                 │
│  ├─▶ Event correlation & fingerprinting                         │
│  └─▶ Polkit helper for privileged operations                    │
└─────────────────────────────────────────────────────────────────┘
```

## Features

### Core System
- **Agent Loop** - LLM-powered reasoning with full provenance tracking
- **Typed Capabilities** - Safe, auditable system operations with risk levels
- **Policy Engine** - Rule-based access control (allow, deny, require_confirmation)
- **Incident Vault** - Decision memory that learns from every interaction

### Knowledge & Memory
- **Man Vault** - SQLite+FTS5 index of ~24,000 man pages with semantic search
- **Incident Memory** - Every action recorded with provenance and outcome
- **Pattern Matching** - Prior successful incidents inform future decisions

### Capability Auto-Generation
- **Package Learning** - `/learn <package>` generates typed capabilities from installed packages
- **Multi-Source Intelligence** - Extracts from dpkg, shell completions, man pages, systemd units
- **Bootstrap on Install** - Core capabilities generated automatically on first run
- **Auto-Learn** - Capabilities generated when new packages are installed

### Automations
- **Reactive Functions** - Event-driven automations from natural language

### Monitoring & Telemetry
- **eBPF Probes** - Kernel-level telemetry (OOM, disk I/O, network, thermal)
- **Watchers** - Journal, kernel, Docker, inotify, package changes
- **Notifications** - ntfy integration for alerts

### Mobile Gateway
- **QR Code Pairing** - Secure device pairing
- **mTLS Authentication** - Mutual TLS with auto-generated certificates
- **Role-Based Access** - Read-only and operator modes with temporary elevation

## Requirements

- Ubuntu 24.04 LTS
- Python 3.10+
- [Ollama](https://ollama.ai) for local LLM inference

## Installation

### 1. Install Ollama

```bash
curl -fsSL https://ollama.ai/install.sh | sh

# Pull the LLM model
ollama pull qwen2.5:7b-instruct-q8_0
```

### 2. Install ELLE

#### From APT Repository (Recommended)

```bash
# Add the GPG key
curl -fsSL https://repo.agentelle.org/elle.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/elle-archive-keyring.gpg

# Add the repository
sudo tee /etc/apt/sources.list.d/elle.sources > /dev/null <<EOF
Types: deb
URIs: https://repo.agentelle.org
Suites: jammy
Components: main
Architectures: amd64
Signed-By: /usr/share/keyrings/elle-archive-keyring.gpg
EOF

# Install ELLE
sudo apt update
sudo apt install elle
```

#### From Source (Development)

```bash
git clone https://github.com/araujota/elle.git
cd elle
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

### Interactive Terminal

```bash
$ elle
ELLE - Ubuntu System Assistant

elle > what is using port 8080?
elle > why is nginx failing?
elle > configure nginx as reverse proxy for localhost:3000
```

### One-shot Commands

```bash
elle "check disk space"
elle status
elle help
```

### Capability Learning

```bash
# Learn a specific package
elle > /learn ffmpeg
Generating capabilities: ffmpeg.convert, ffmpeg.probe, ffmpeg.stream

# Learn all installed packages
elle > /learn --all

# Approve a capability for use
elle > /learn approve ffmpeg.convert
```

### Reactive Functions

```bash
# Create event-driven automations
elle > /react create "when disk > 90%, clean docker images and notify me"

# Manage reactive functions
elle > /react list
elle > /react enable disk-cleanup
```

### Mobile Gateway

```bash
elle > /mobile up          # Start gateway with QR code
elle > /mobile devices     # List paired devices
elle > /mobile approve <device> --ttl 30m  # Grant elevation
```

## Safety

ELLE includes multiple safety mechanisms:
- **Command denylist** - Blocks dangerous commands (rm -rf /, fork bombs, etc.)
- **Policy enforcement** - All capabilities go through the Policy Engine
- **Incident tracking** - Every action recorded with full provenance
- **No implicit sudo** - Privileged operations require explicit confirmation
- **Preview before apply** - Configuration changes show diffs before execution

## Development

```bash
source .venv/bin/activate
pytest                    # Run tests
pytest --cov=elle         # With coverage
ruff check src/           # Lint
ruff format src/          # Format
mypy src/                 # Type check
```

## Support ELLE

ELLE is open source and free to use. If you find it valuable, consider sponsoring development:

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor_on_GitHub-❤-ea4aaa?style=for-the-badge&logo=github)](https://github.com/sponsors/araujota)

**Questions or feedback?** Email: araujota97@gmail.com

## License

GPL-3.0-or-later

## Links

- **Website:** https://araujota.github.io/elle
- **Repository:** https://github.com/araujota/elle
- **Sponsor:** https://github.com/sponsors/araujota
