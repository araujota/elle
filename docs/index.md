---
layout: default
title: ELLE - Local-First System Intelligence
---

# ELLE

**Enabling Layer Learning Everything** — a local-first, agentic system layer for Ubuntu that converts kernel-level telemetry into natural language insight and safe system operations.

## What is ELLE?

ELLE is your AI-powered system administrator that runs entirely on your machine. No cloud, no subscriptions, no data leaving your system.

- **Ask questions in plain English** — "How much disk space is left?" or "Why is the server slow?"
- **Execute tasks safely** — ELLE explains what it will do, asks for confirmation, and can roll back changes
- **Learn from experience** — The Incident Vault remembers what worked so ELLE gets smarter over time
- **Stay informed** — Real-time monitoring alerts you to issues before they become problems

## Quick Start

```bash
# Add the ELLE repository
curl -fsSL https://apt.elle.dev/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/elle-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/elle-archive-keyring.gpg] https://apt.elle.dev stable main" | sudo tee /etc/apt/sources.list.d/elle.list

# Install ELLE
sudo apt update
sudo apt install elle

# Install Ollama (required for AI features)
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull qwen2.5:7b-instruct-q8_0

# Start the daemon and launch ELLE
sudo systemctl start elled
elle
```

## Features

### Natural Language Interface
Ask questions or give commands in plain English. ELLE understands context and intent.

```
elle> how much memory is being used?
elle> enable the firewall with default deny
elle> why did nginx crash last night?
```

### Safety First
Every system-modifying action follows the **Explain → Plan → Confirm → Apply** workflow. Dangerous commands are blocked. Privileged operations require authentication.

### Local AI
ELLE uses [Ollama](https://ollama.ai) for on-device inference. Your data never leaves your machine. Works offline after initial model download.

### Decision Memory
The Incident Vault stores what happened, what you decided, and how it turned out. ELLE learns from your environment and gets better at helping you.

## Requirements

- Ubuntu 24.04 LTS or 22.04 LTS
- 16 GB RAM (8 GB minimum)
- 20 GB free disk space
- [Ollama](https://ollama.ai) for AI features

## Documentation

- [Installation Guide](install)
- [User Documentation](docs)
- [Hardware Requirements](docs/hardware)

## License

ELLE is open source software licensed under the [GPL-3.0](https://github.com/araujota/elle/blob/main/LICENSE).
