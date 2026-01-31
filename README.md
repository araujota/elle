# ELLE

**Enabling Layer Learning Everything**

[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org)
[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-ea4aaa?logo=github)](https://github.com/sponsors/araujota)

A local-first, agentic system layer for Ubuntu 22.04+ LTS that converts kernel-level telemetry into natural language insight and safe system operations.

## What is ELLE?

ELLE is an AI-powered system administrator that runs entirely on your machine. It watches your system's telemetry in real time, reasons over it with a local LLM, and executes safe operations through a typed capability system — all with full provenance tracking and decision memory. No cloud dependency, no data leaving your machine.

## Key features

- **Natural language interface** — Ask questions or give commands in plain English
- **Agentic execution** — Plans multi-step operations and executes via typed, policy-governed capabilities
- **Decision memory** — PostgreSQL + pgvector-backed Incident Vault records every action with full provenance, enabling similarity search and learning from experience
- **Real-time telemetry** — Journal, kernel, eBPF, Docker, and periodic probes monitored by the daemon
- **Reactive functions** — Event-driven automations defined in natural language
- **Safety first** — Command denylist, risk levels, policy engine, Polkit (never sudo)
- **Capability auto-generation** — `/learn` generates typed operations from installed packages
- **Team knowledge sharing** — Anonymized incidents synced via self-hosted elle-cloud over mTLS

## Quick start

```bash
# Install Ollama and pull the model
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull qwen2.5:7b-instruct-q8_0

# Install ELLE (from source)
git clone https://github.com/araujota/elle.git && cd elle
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Launch
elle
```

See the [Installation wiki page](https://github.com/araujota/elle/wiki/Installation) for APT repository setup and full instructions.

## How it works

Every interaction flows through **The Spine**:

```
DAEMON → SIGNALS → INCIDENT REPORT → AGENT LOOP → CAPABILITIES → OUTCOME → INCIDENT MEMORY
```

The daemon captures telemetry. The Agent Loop reasons over it with documentation and past decisions. Capabilities execute safe, typed operations through the Policy Engine. Outcomes are recorded for future learning.

## Documentation

Full documentation lives in the [GitHub Wiki](https://github.com/araujota/elle/wiki):

- **[Home](https://github.com/araujota/elle/wiki)** — Overview and quick links
- **[Why ELLE](https://github.com/araujota/elle/wiki/Why-ELLE)** — Philosophy and motivation
- **[Installation](https://github.com/araujota/elle/wiki/Installation)** — Prerequisites, install methods, hardware requirements
- **[Setup Wizard](https://github.com/araujota/elle/wiki/Setup-Wizard)** — First-run configuration walkthrough
- **[Configuration](https://github.com/araujota/elle/wiki/Configuration)** — Complete `elle.toml` reference
- **[LLM Providers](https://github.com/araujota/elle/wiki/LLM-Providers)** — Ollama, OpenAI-compatible, fallback chain
- **[Architecture](https://github.com/araujota/elle/wiki/Architecture)** — The Spine, Three Pillars, data flow
- **[Capabilities](https://github.com/araujota/elle/wiki/Capabilities)** — Typed operations, domains, policy engine
- **[Reactive Functions](https://github.com/araujota/elle/wiki/Reactive-Functions)** — Event-driven automations
- **[Telemetry](https://github.com/araujota/elle/wiki/Telemetry)** — What ELLE monitors, event sources, C daemon
- **[Security](https://github.com/araujota/elle/wiki/Security)** — Safety model, privilege levels, denylist
- **[Private Incident Vault](https://github.com/araujota/elle/wiki/Private-Incident-Vault)** — elle-cloud deployment, certs, team setup

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                    # Run tests
ruff check src/           # Lint
ruff format src/          # Format
mypy src/                 # Type check
```

## Support ELLE

ELLE is open source and free to use. If you find it valuable, consider sponsoring development:

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor_on_GitHub-ea4aaa?style=for-the-badge&logo=github)](https://github.com/sponsors/araujota)

Questions or feedback? Email: araujota97@gmail.com

## License

[GPL-3.0-or-later](https://www.gnu.org/licenses/gpl-3.0)
