# ELLE

**Enabling Layer Learning Everything** — a local-first, agentic system layer for Ubuntu 22.04+ LTS that converts kernel-level telemetry into natural language insight and safe system operations.

## What is ELLE?

ELLE is an AI-powered system administrator that runs entirely on your machine. You interact with it through natural language — ask questions about your system, diagnose problems, or give it tasks to execute. ELLE reasons over live telemetry, documentation, and its own decision history to produce grounded answers and safe operations.

Unlike monitoring dashboards or alerting tools, ELLE is *agentic*: it creates plans, executes typed operations through a policy engine, and records every decision with full provenance. Unlike cloud-based AI assistants, ELLE runs locally via [Ollama](https://ollama.ai) — no data leaves your machine.

## Who is it for?

- **Sysadmins** managing Ubuntu servers who want an assistant that understands their system's state
- **DevOps engineers** who want event-driven automations defined in natural language
- **Homelabbers** who want a smarter way to manage their infrastructure
- **Teams** who want to share anonymized incident knowledge across installations

## What makes it different?

- **Local-first** — All inference runs on your hardware via Ollama. No cloud dependency, no subscriptions, no data exfiltration.
- **Agentic** — ELLE doesn't just answer questions. It plans multi-step operations, executes them through typed capabilities, and verifies outcomes.
- **Self-learning** — The Incident Vault records every interaction with provenance and outcome. When similar issues arise, ELLE recalls what worked before.
- **Safe by design** — Every mutation goes through the Capability system with policy enforcement, risk levels, and audit trails. Dangerous commands are blocked. Privileged operations use Polkit, never sudo.

## Quick links

| Page | Description |
|------|-------------|
| [[Installation]] | Get ELLE running on your system |
| [[Setup Wizard|Setup-Wizard]] | Walkthrough of the first-run configuration |
| [[Architecture]] | How The Spine pipeline and Three Pillars work |
| [[Configuration]] | Complete `elle.toml` reference |
| [[LLM Providers|LLM-Providers]] | Ollama setup, model selection, OpenAI-compatible providers |
| [[Capabilities]] | The typed operation system |
| [[Reactive Functions|Reactive-Functions]] | Event-driven automations |
| [[Telemetry]] | What ELLE monitors and how |
| [[Security]] | Safety model, privilege levels, denylist |
| [[Private Incident Vault|Private-Incident-Vault]] | Shared decision memory via elle-cloud |

## Project status

ELLE is under active development targeting Ubuntu 22.04+ LTS. The core architecture (The Spine, Capabilities, Agent Loop, Incident Vault) is stable. See the [GitHub repository](https://github.com/araujota/elle) for current progress.
