---
title: ELLE
---

<div class="hero">
  <h1><span class="logo-icon">&#x25C8;</span> ELLE</h1>
  <p class="tagline">A local-first, agentic system layer for Ubuntu 22.04+ LTS that converts kernel-level telemetry into natural language insight and safe system operations.</p>
  <div class="hero-buttons">
    <a href="{{ site.github_wiki }}/Installation" class="btn btn-large">Get Started</a>
    <a href="{{ site.github_repo }}" class="btn btn-large btn-outline" target="_blank">View on GitHub</a>
  </div>
</div>

## What is ELLE?

ELLE is an AI-powered system administrator that runs entirely on your machine. It watches your system's telemetry in real time, reasons over it with a local LLM, and executes safe operations through a typed capability system -- all with full provenance tracking and decision memory.

No cloud dependency. No data leaving your machine.

<div class="row" style="margin-top: 2rem;">
  <div class="col s12 m6 l4">
    <div class="feature-card">
      <i class="material-icons feature-icon">chat</i>
      <h4>Natural Language</h4>
      <p>Ask questions or give commands in plain English. ELLE understands system context and responds with actionable insight.</p>
    </div>
  </div>
  <div class="col s12 m6 l4">
    <div class="feature-card">
      <i class="material-icons feature-icon">psychology</i>
      <h4>Agentic Execution</h4>
      <p>Plans multi-step operations and executes via typed, policy-governed capabilities with full audit trails.</p>
    </div>
  </div>
  <div class="col s12 m6 l4">
    <div class="feature-card">
      <i class="material-icons feature-icon">storage</i>
      <h4>Decision Memory</h4>
      <p>PostgreSQL + pgvector-backed Incident Vault records every action with provenance, enabling similarity search and learning.</p>
    </div>
  </div>
  <div class="col s12 m6 l4">
    <div class="feature-card">
      <i class="material-icons feature-icon">sensors</i>
      <h4>Real-Time Telemetry</h4>
      <p>Journal, kernel, eBPF, Docker, and periodic probes monitored by the daemon. Detects anomalies and forecasts resource exhaustion.</p>
    </div>
  </div>
  <div class="col s12 m6 l4">
    <div class="feature-card">
      <i class="material-icons feature-icon">security</i>
      <h4>Safety First</h4>
      <p>Command denylist, risk levels, policy engine, and Polkit integration. Never sudo. Preview before apply.</p>
    </div>
  </div>
  <div class="col s12 m6 l4">
    <div class="feature-card">
      <i class="material-icons feature-icon">auto_fix_high</i>
      <h4>Self-Building</h4>
      <p>Capabilities grow via <code>/learn</code>. Incident memory grows from every interaction. Reactive functions are user-defined automations.</p>
    </div>
  </div>
</div>

## How It Works

Every interaction flows through **The Spine**:

```
DAEMON --> SIGNALS --> INCIDENT REPORT --> AGENT LOOP --> CAPABILITIES --> OUTCOME --> INCIDENT MEMORY
```

The daemon captures telemetry. The Agent Loop reasons over it with documentation and past decisions. Capabilities execute safe, typed operations through the Policy Engine. Outcomes are recorded for future learning.

## Quick Start

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

See the [Installation guide]({{ site.github_wiki }}/Installation) for APT repository setup and full instructions.

## Documentation

Full documentation lives in the [GitHub Wiki]({{ site.github_wiki }}):

| Page | Description |
|------|-------------|
| [Why ELLE]({{ site.github_wiki }}/Why-ELLE) | Philosophy and motivation |
| [Installation]({{ site.github_wiki }}/Installation) | Prerequisites, install methods, hardware requirements |
| [Setup Wizard]({{ site.github_wiki }}/Setup-Wizard) | First-run configuration walkthrough |
| [Configuration]({{ site.github_wiki }}/Configuration) | Complete `elle.toml` reference |
| [LLM Providers]({{ site.github_wiki }}/LLM-Providers) | Ollama, OpenAI-compatible, fallback chain |
| [Architecture]({{ site.github_wiki }}/Architecture) | The Spine, Three Pillars, data flow |
| [Capabilities]({{ site.github_wiki }}/Capabilities) | Typed operations, domains, policy engine |
| [Reactive Functions]({{ site.github_wiki }}/Reactive-Functions) | Event-driven automations |
| [Telemetry]({{ site.github_wiki }}/Telemetry) | What ELLE monitors, event sources, C daemon |
| [Security]({{ site.github_wiki }}/Security) | Safety model, privilege levels, denylist |
| [Private Incident Vault]({{ site.github_wiki }}/Private-Incident-Vault) | elle-cloud deployment, certs, team setup |

## License

[GPL-3.0-or-later]({{ site.github_repo }}/blob/main/LICENSE)
