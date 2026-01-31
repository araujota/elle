# Why ELLE

## The problem

Servers generate enormous amounts of telemetry — journal logs, kernel messages, container events, resource metrics — but most of it goes unread until something breaks. Administrators context-switch between terminals, documentation, and past incident notes to piece together what happened and what to do. The knowledge of *how* a problem was solved lives in tribal memory: chat threads, personal notes, or someone's head.

Existing tools address parts of this:
- **Monitoring dashboards** (Grafana, Datadog) show metrics but don't reason about them
- **Alerting systems** (PagerDuty, Alertmanager) tell you something is wrong but not why or what to do
- **Configuration management** (Ansible, Puppet) automates known procedures but can't adapt to novel situations
- **ChatGPT/cloud AI** can reason but doesn't see your system state, requires sending data off-machine, and has no memory of your environment

None of these are an *agentic layer* that sits on the machine, observes telemetry in real time, reasons over it with contextual knowledge, takes safe action, and learns from the outcome.

## The insight

Large language models can reason over system state — but only if they're given the right pipeline. Raw log lines and metrics aren't enough. The model needs:

1. **Normalized events** — categorized, deduplicated, fingerprinted telemetry
2. **Contextual knowledge** — man pages, documentation, prior incident decisions
3. **Safe execution primitives** — typed operations with risk levels and policy enforcement
4. **Outcome tracking** — recording what was tried and whether it worked

If you build this pipeline correctly, the LLM becomes a system administrator that gets better over time.

## The approach

ELLE is built around three commitments:

### Local-first

All inference runs on-machine via Ollama. Your telemetry, logs, and system state never leave your hardware. There's no cloud dependency, no API key required for basic operation, and no subscription. ELLE works fully offline after initial setup.

### Agentic

ELLE doesn't just answer questions. When you ask it to do something, it:
1. Creates an Incident Report for provenance
2. Searches documentation (Man Vault) and past decisions (Incident Vault)
3. Reasons over the context with the LLM
4. Builds a plan using typed Capabilities
5. Executes through the Policy Engine (with confirmation for risky operations)
6. Records the outcome to Incident Memory

This is The Spine — every interaction flows through the same pipeline regardless of whether it was triggered by a user command, a telemetry event, or a reactive function.

### Self-learning

The Incident Vault stores every decision with:
- What happened (symptoms, telemetry events, system state snapshot)
- What was decided (chosen approach, rationale, confidence breakdown)
- What sources informed the decision (man page citations, prior incidents, telemetry)
- What the outcome was (improved, partial, no change, worse)

When a similar situation arises — matched by fingerprint similarity — ELLE retrieves these records and weighs proven solutions higher. The system gets better with use.

## The architecture mandate

ELLE enforces a strict rule: **everything flows through The Spine**. There are no hardcoded diagnostic handlers, no direct subprocess calls that bypass capabilities, no file operations that skip the audit trail. If functionality doesn't flow through the pipeline, it gets refactored or removed.

This constraint keeps the system coherent as it grows. New capabilities, new telemetry sources, and new automations all integrate through the same pipeline and benefit from the same policy enforcement, provenance tracking, and incident memory.

## Comparison to existing tools

| Tool type | What it does | What ELLE adds |
|-----------|-------------|----------------|
| Monitoring (Grafana) | Visualizes metrics | Reasons about metrics, takes action |
| Alerting (PagerDuty) | Notifies on threshold | Diagnoses root cause, suggests fix |
| Config management (Ansible) | Runs known playbooks | Adapts to novel situations via LLM reasoning |
| Cloud AI (ChatGPT) | General reasoning | Sees live system state, runs locally, has incident memory |
| Log analysis (ELK) | Searches logs | Correlates events, creates incidents, executes fixes |

ELLE is not a replacement for these tools. It's a layer that sits between your system and your decision-making, turning raw signals into contextual insight and safe operations.
