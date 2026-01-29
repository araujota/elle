---
layout: default
title: Local-First System Intelligence
---

<div class="hero">
  <h1><span class="logo-icon">&#x25C8;</span> ELLE</h1>
  <p class="tagline">
    <strong>Enabling Layer Learning Everything</strong> &mdash; a local-first, agentic system layer for Ubuntu that converts kernel-level telemetry into natural language insight and safe system operations.
  </p>
  <div class="hero-buttons">
    <a href="{{ '/install' | relative_url }}" class="btn btn-large waves-effect waves-light">
      <i class="material-icons left">rocket_launch</i>Get Started
    </a>
    <a href="{{ site.github_repo }}" target="_blank" class="btn btn-large btn-outline waves-effect">
      <i class="material-icons left">code</i>View on GitHub
    </a>
  </div>
</div>

---

## What is ELLE?

ELLE is your **AI-powered system administrator** that runs entirely on your machine. No cloud, no subscriptions, no data leaving your system.

<div class="row">
  <div class="col s12 m6 l3">
    <div class="feature-card">
      <i class="material-icons feature-icon">chat</i>
      <h4>Natural Language</h4>
      <p>Ask questions or give commands in plain English. ELLE understands context, intent, and your system's state.</p>
    </div>
  </div>
  <div class="col s12 m6 l3">
    <div class="feature-card">
      <i class="material-icons feature-icon">security</i>
      <h4>Safety First</h4>
      <p>Every system-modifying action follows <strong>Explain → Plan → Confirm → Apply</strong>. Dangerous commands are blocked.</p>
    </div>
  </div>
  <div class="col s12 m6 l3">
    <div class="feature-card">
      <i class="material-icons feature-icon">memory</i>
      <h4>Self-Learning</h4>
      <p>The Incident Vault remembers what worked, learning from your environment to get smarter over time.</p>
    </div>
  </div>
  <div class="col s12 m6 l3">
    <div class="feature-card">
      <i class="material-icons feature-icon">bolt</i>
      <h4>Reactive Functions</h4>
      <p>Create event-driven automations in natural language. ELLE responds to system events automatically.</p>
    </div>
  </div>
</div>

---

## See It In Action

<div class="terminal-demo">
  <div class="terminal-header">
    <span class="terminal-dot red"></span>
    <span class="terminal-dot yellow"></span>
    <span class="terminal-dot green"></span>
    <span class="terminal-title">elle - bash</span>
  </div>
  <div class="terminal-body">
    <div><span class="prompt">elle&gt;</span> <span class="command">why is my disk almost full?</span></div>
    <div class="output">
      Analyzing disk usage on /dev/sda1...<br><br>
      <strong>Root Cause:</strong> Docker images consuming 45GB in /var/lib/docker<br>
      <strong>Top offenders:</strong><br>
      &nbsp;&nbsp;• node:18-alpine (unused, 890MB)<br>
      &nbsp;&nbsp;• postgres:13 (dangling, 420MB)<br>
      &nbsp;&nbsp;• Build cache (12GB)<br><br>
      <strong>Suggested fix:</strong> Run `docker system prune` to reclaim ~15GB
    </div>
    <br>
    <div><span class="prompt">elle&gt;</span> <span class="command">/react create</span></div>
    <div class="output">
      What should trigger this automation?
    </div>
    <div><span class="prompt">&gt;</span> <span class="command">when disk usage exceeds 85%</span></div>
    <div class="output">
      What should happen?
    </div>
    <div><span class="prompt">&gt;</span> <span class="command">clean docker cache and notify me</span></div>
    <div class="output">
      <span style="color: #3fb950;">✓</span> Created reactive function: disk-cleanup-85<br>
      Trigger: disk_usage > 85%<br>
      Actions: docker.prune, notification.send
    </div>
  </div>
</div>

---

## Key Features

<div class="row">
  <div class="col s12">
    <ul class="collapsible">
      <li>
        <div class="collapsible-header"><i class="material-icons">auto_fix_high</i>Reactive Functions</div>
        <div class="collapsible-body">
          <p>Create event-driven automations using natural language. ELLE monitors your system and responds automatically to events like:</p>
          <ul>
            <li><strong>Disk alerts:</strong> "When disk usage exceeds 90%, clean apt cache and old logs"</li>
            <li><strong>Docker crashes:</strong> "When a container dies unexpectedly, diagnose it and notify me"</li>
            <li><strong>Service failures:</strong> "When nginx fails, restart it and log the incident"</li>
            <li><strong>Scheduled tasks:</strong> "Every Sunday at 3am, run system updates"</li>
          </ul>
          <p>All reactive functions are policy-governed, auditable, and can require confirmation before executing.</p>
        </div>
      </li>
      <li>
        <div class="collapsible-header"><i class="material-icons">psychology</i>Decision Memory (Incident Vault)</div>
        <div class="collapsible-body">
          <p>ELLE learns from every interaction. The Incident Vault stores:</p>
          <ul>
            <li>What happened (error messages, system state)</li>
            <li>What you decided to do</li>
            <li>Whether it worked</li>
          </ul>
          <p>When similar issues arise, ELLE recalls prior decisions and suggests proven solutions. Your system administration knowledge accumulates over time.</p>
        </div>
      </li>
      <li>
        <div class="collapsible-header"><i class="material-icons">inventory_2</i>Capabilities System</div>
        <div class="collapsible-body">
          <p>Every system operation is a typed, policy-governed <strong>Capability</strong>:</p>
          <ul>
            <li><code>service.restart</code> — Restart systemd services</li>
            <li><code>package.install</code> — Install packages via apt</li>
            <li><code>docker.prune</code> — Clean up Docker resources</li>
            <li><code>config.edit</code> — Safely edit configuration files</li>
            <li><code>wireguard.generate-key</code> — Generate VPN keys</li>
          </ul>
          <p>Capabilities declare their risk level, side effects, and rollback procedures. The policy engine enforces rules like "high-risk operations require confirmation."</p>
        </div>
      </li>
      <li>
        <div class="collapsible-header"><i class="material-icons">sensors</i>Real-Time Telemetry</div>
        <div class="collapsible-body">
          <p>The <code>elled</code> daemon monitors your system continuously:</p>
          <ul>
            <li><strong>System Journal:</strong> Errors, warnings, and service status</li>
            <li><strong>Kernel Messages:</strong> OOM kills, hardware errors, thermal events</li>
            <li><strong>Docker Events:</strong> Container starts, stops, crashes, health changes</li>
            <li><strong>Network Probes:</strong> Port status, connectivity checks</li>
            <li><strong>eBPF Tracing:</strong> Advanced syscall-level monitoring (optional)</li>
          </ul>
          <p>Events are normalized, deduplicated, and stored for analysis. ELLE can alert you before problems become critical.</p>
        </div>
      </li>
      <li>
        <div class="collapsible-header"><i class="material-icons">lock</i>Polkit Integration</div>
        <div class="collapsible-body">
          <p>ELLE never uses <code>sudo</code>. Privileged operations go through Polkit, which:</p>
          <ul>
            <li>Prompts for your password (with 5-minute caching)</li>
            <li>Logs all privileged actions</li>
            <li>Can be configured per-action or per-user</li>
          </ul>
          <p>During setup, you can choose between:</p>
          <ul>
            <li><strong>Secure:</strong> Always require password (default)</li>
            <li><strong>Convenient:</strong> Group-based authentication (members of 'elle' group skip password)</li>
            <li><strong>Passwordless:</strong> No prompts (for dev machines only)</li>
          </ul>
        </div>
      </li>
    </ul>
  </div>
</div>

---

## Quick Start

```bash
# Add the GPG key
curl -fsSL https://repo.agentelle.org/elle.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/elle-archive-keyring.gpg

# Add the repository (DEB822 format)
sudo tee /etc/apt/sources.list.d/elle.sources > /dev/null <<EOF
Types: deb
URIs: https://repo.agentelle.org
Suites: jammy
Components: main
Architectures: amd64
Signed-By: /usr/share/keyrings/elle-archive-keyring.gpg
EOF

# Install ELLE
sudo apt update && sudo apt install elle

# Install Ollama for AI features
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull qwen2.5:7b-instruct-q8_0

# Start ELLE
sudo systemctl start elled
elle
```

On first launch, ELLE's setup wizard guides you through configuration — safety preferences, telemetry sources, privilege levels, and more.

<div style="text-align: center; margin: 2rem 0;">
  <a href="{{ '/install' | relative_url }}" class="btn btn-large waves-effect waves-light">
    <i class="material-icons left">arrow_forward</i>Full Installation Guide
  </a>
</div>

---

## Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **OS** | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| **RAM** | 8 GB | 16 GB |
| **Storage** | 15 GB | 25 GB |
| **CPU** | 4 cores | 8 cores |

ELLE uses [Ollama](https://ollama.ai) for local AI inference. GPU acceleration is supported but not required.

<a href="{{ '/docs/hardware' | relative_url }}" class="btn-flat waves-effect">View detailed hardware requirements →</a>

---

## Support ELLE Development

<div class="card">
  <div class="card-content">
    <span class="card-title"><i class="material-icons left" style="color: var(--sponsor-color);">favorite</i>Become a Sponsor</span>
    <p>ELLE is open source and free to use. If you find it valuable, consider sponsoring development to help keep the project sustainable.</p>
  </div>
  <div class="card-action">
    <a href="{{ site.github_sponsor }}" target="_blank"><i class="material-icons left">favorite</i>Sponsor on GitHub</a>
    <a href="{{ site.github_repo }}">View Source</a>
  </div>
</div>

---

## Documentation

- **[Installation Guide]({{ '/install' | relative_url }})** — Get ELLE running on your system
- **[User Documentation]({{ '/docs' | relative_url }})** — Commands, configuration, and features
- **[Hardware Requirements]({{ '/docs/hardware' | relative_url }})** — System specifications and supported platforms

## License

ELLE is open source software licensed under the [GPL-3.0]({{ site.github_repo }}/blob/main/LICENSE).
