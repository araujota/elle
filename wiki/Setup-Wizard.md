# Setup Wizard

On first launch, ELLE runs an interactive setup wizard that guides you through configuration. You can re-run it at any time with `/reconfigure`.

## Overview

The wizard has 6 steps:

1. **Environment Check** — Verifies prerequisites
2. **Safety Settings** — Safety level, confirmation preferences, config preview
3. **Telemetry Sources** — Which system signals to monitor
4. **Optional Features** — REST API, auto-learn packages
5. **Privilege Configuration** — Polkit authentication level
6. **Review & Confirm** — Summary and apply

You can navigate back to any previous step before confirming.

## Step 1: Environment check

The wizard verifies that your system is ready:

- **Python version** — 3.10+ required
- **Ollama** — Running and accessible
- **LLM model** — `qwen2.5:7b-instruct-q8_0` pulled
- **telemetryd** — C telemetry daemon available (optional)
- **elled** — Python daemon service

If Ollama isn't running or the model isn't pulled, the wizard offers to start Ollama and pull the model for you.

## Step 2: Safety settings

### Safety level

| Level | Description |
|-------|-------------|
| **Standard** (recommended) | Blocks dangerous commands (rm -rf /, fork bombs, etc.) and requires confirmation for high-risk operations. Good balance of safety and usability. |
| **Cautious** | Maximum protection. Requires confirmation for most system changes and previews for all config edits. Best for production servers. |
| **Minimal** | Only blocks the most dangerous patterns. For experienced users who want more freedom. |

### Confirmation preference

| Setting | Description |
|---------|-------------|
| **Always confirm** | Ask before any system change, even low-risk ones |
| **High-risk only** (recommended) | Only ask for operations that could impact system stability |
| **Never** | Execute without confirmation. Use with caution. |

### Config preview

When enabled (default), ELLE shows a unified diff of proposed configuration changes before applying them. This lets you review exactly what will change.

## Step 3: Telemetry sources

Choose which system signals ELLE monitors:

| Source | Description | Default |
|--------|-------------|---------|
| **System Journal** | Monitor systemd journal for errors and warnings | On |
| **Kernel Messages** | Watch for kernel-level events (OOM, hardware errors) | On |
| **System Probes** | Periodic checks for disk space, memory, network, thermal | On |
| **Docker Events** | Monitor container state changes and health | On |
| **eBPF Tracing** | Advanced kernel tracing for detailed diagnostics. Requires kernel 5.8+ | Off |

See [[Telemetry]] for details on what each source captures.

## Step 4: Optional features

| Feature | Description | Default |
|---------|-------------|---------|
| **REST API** | OpenAI-compatible API endpoint for external integrations. Bound to localhost by default. | On |
| **Auto-Learn Packages** | Automatically generate capabilities when new packages are installed. ELLE monitors for package changes and learns them in the background. | On |

## Step 5: Privilege configuration

ELLE never uses `sudo`. Privileged operations go through Polkit.

| Level | Description |
|-------|-------------|
| **Secure** (recommended) | Always require password for privileged operations. Polkit caches authentication for 5 minutes per session. |
| **Convenient** | Members of the `elle` group can perform ELLE operations without password prompts. You will be added to this group. Requires logout/login. |
| **Passwordless** | No password required for any ELLE privileged operation. Only use on single-user systems or development machines. |

### LLM provider

The wizard also lets you choose between:

| Provider | Description |
|----------|-------------|
| **Local Ollama** (recommended) | Run inference locally. Keeps all data on-device. |
| **Remote OpenAI-Compatible** | Use a remote endpoint (OpenAI, Azure, vLLM, LM Studio). Requires network access and API key. Local Ollama can be used as fallback. |

See [[LLM Providers|LLM-Providers]] for full provider configuration details.

## Step 6: Review & confirm

The wizard shows a summary of all chosen settings. After confirmation:

1. Configuration is written to `~/.config/elle/elle.toml`
2. Policy rules are written to `~/.config/elle/policy.yaml` (if applicable)
3. Polkit rules are installed (if convenient/passwordless selected)
4. Ollama is started (if not running)
5. The LLM model is pulled (if not present)
6. Daemons are started

## Configuration files

The wizard writes to:

| File | Purpose |
|------|---------|
| `~/.config/elle/elle.toml` | User configuration (overrides system defaults) |
| `~/.config/elle/policy.yaml` | User policy rules |
| `/etc/polkit-1/rules.d/50-elle.rules` | Polkit rules (if convenient/passwordless) |

## Re-running the wizard

```
elle> /reconfigure
```

This re-opens the wizard with your current settings pre-loaded. You can change any setting and re-apply.
