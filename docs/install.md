---
layout: default
title: Quick Start
---

# Quick Start

Get ELLE running on your Ubuntu system in minutes.

---

## From APT Repository (Recommended)

<div class="callout callout-info">
<strong>Best for most users</strong><br>
The APT repository provides automatic updates and proper system integration.
</div>

### 1. Add the Repository

```bash
# Download and install the GPG key
curl -fsSL https://apt.elle.dev/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/elle-archive-keyring.gpg

# Add the repository
echo "deb [signed-by=/usr/share/keyrings/elle-archive-keyring.gpg] https://apt.elle.dev stable main" | sudo tee /etc/apt/sources.list.d/elle.list
```

### 2. Install ELLE

```bash
sudo apt update
sudo apt install elle
```

### 3. Install Ollama

ELLE requires Ollama for AI inference. Install it from [ollama.ai](https://ollama.ai):

```bash
curl -fsSL https://ollama.ai/install.sh | sh
```

### 4. Download AI Models

ELLE uses two models: a fast classifier and a capable generator.

```bash
# Classification model (required, ~4GB)
ollama pull phi3.5:3.8b-mini-instruct-q8_0

# Generation model (required, ~8GB)
ollama pull qwen2.5:7b-instruct-q8_0
```

### 5. Start ELLE

```bash
# Start the daemon
sudo systemctl start elled
sudo systemctl enable elled  # Optional: start on boot

# Launch ELLE
elle
```

---

## First-Run Setup Wizard

On first launch, ELLE guides you through configuration with an interactive setup wizard.

<div class="terminal-demo">
  <div class="terminal-header">
    <span class="terminal-dot red"></span>
    <span class="terminal-dot yellow"></span>
    <span class="terminal-dot green"></span>
    <span class="terminal-title">ELLE Setup Wizard</span>
  </div>
  <div class="terminal-body">
    <div class="output" style="border: 1px solid #30363d; border-radius: 8px; padding: 1rem; margin-bottom: 1rem;">
      <strong style="color: #79c0ff;">Welcome to ELLE</strong><br><br>
      This quick setup will help you configure ELLE for your needs.<br>
      You can change any of these settings later with /reconfigure.
    </div>
    <div><span style="color: #3fb950;">✓</span> Python 3.11</div>
    <div><span style="color: #3fb950;">✓</span> Ollama is running</div>
    <div><span style="color: #8b949e;">&nbsp;&nbsp;• Models: phi3.5:3.8b, qwen2.5:7b</span></div>
  </div>
</div>

### What the Wizard Configures

<div class="row">
  <div class="col s12 m6">
    <div class="card">
      <div class="card-content">
        <span class="card-title"><i class="material-icons left">security</i>Safety Settings</span>
        <ul>
          <li><strong>Safety Level:</strong> Standard, Cautious, or Minimal</li>
          <li><strong>Confirmation Prompts:</strong> Always, High-risk only, or Never</li>
          <li><strong>Config Preview:</strong> Show diffs before editing files</li>
        </ul>
      </div>
    </div>
  </div>
  <div class="col s12 m6">
    <div class="card">
      <div class="card-content">
        <span class="card-title"><i class="material-icons left">sensors</i>Telemetry Sources</span>
        <ul>
          <li><strong>System Journal:</strong> Monitor systemd logs</li>
          <li><strong>Kernel Messages:</strong> Watch for OOM, hardware errors</li>
          <li><strong>Docker Events:</strong> Track container lifecycle</li>
          <li><strong>eBPF Tracing:</strong> Advanced syscall monitoring</li>
        </ul>
      </div>
    </div>
  </div>
</div>

<div class="row">
  <div class="col s12 m6">
    <div class="card">
      <div class="card-content">
        <span class="card-title"><i class="material-icons left">extension</i>Optional Features</span>
        <ul>
          <li><strong>REST API:</strong> OpenAI-compatible endpoint</li>
          <li><strong>GUI Automation:</strong> Control desktop apps via AT-SPI</li>
        </ul>
      </div>
    </div>
  </div>
  <div class="col s12 m6">
    <div class="card">
      <div class="card-content">
        <span class="card-title"><i class="material-icons left">lock</i>Privilege Configuration</span>
        <ul>
          <li><strong>Secure:</strong> Always require password (default)</li>
          <li><strong>Convenient:</strong> Group-based authentication</li>
          <li><strong>Passwordless:</strong> No prompts (dev machines)</li>
        </ul>
      </div>
    </div>
  </div>
</div>

---

## From Source

For development or if you prefer building from source:

### Prerequisites

```bash
sudo apt install python3.11 python3.11-venv python3-pip git libaugeas-dev
```

### Clone and Install

```bash
git clone https://github.com/araujota/elle.git
cd elle
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Run

```bash
# In development mode
elle

# Run the daemon manually
elled
```

---

## Verify Installation

After installation, verify everything is working:

```bash
# Check ELLE version
elle --version

# Check daemon status
systemctl status elled

# Check Ollama models
ollama list

# Launch ELLE
elle
```

You should see the setup wizard on first launch, or the ELLE prompt if you've already configured it:

```
elle>
```

---

## Post-Installation

### Privilege Levels

ELLE never uses `sudo`. Privileged operations go through Polkit. During setup, you chose a privilege level:

| Level | Description | When to Use |
|-------|-------------|-------------|
| **Secure** | Always prompts for password | Production servers, shared machines |
| **Convenient** | 'elle' group members skip password | Personal workstations |
| **Passwordless** | No authentication required | Development VMs, single-user systems |

To change privilege settings later:

```bash
elle
elle> /reconfigure
```

### Configuration Files

| File | Purpose |
|------|---------|
| `/etc/elle/elle.toml` | System-wide configuration |
| `~/.config/elle/elle.toml` | User overrides |
| `~/.config/elle/policy.yaml` | User policy rules |
| `/etc/polkit-1/rules.d/50-elle.rules` | Polkit rules (if convenient/passwordless) |

---

## Uninstall

```bash
# Remove ELLE (keeps data)
sudo apt remove elle

# Remove ELLE and all data
sudo apt purge elle

# This removes:
# - The elle and elled binaries
# - Configuration in /etc/elle/
# - State data in /var/lib/elle/
# - The elle system user
```

---

## Troubleshooting

<ul class="collapsible">
  <li>
    <div class="collapsible-header"><i class="material-icons">error_outline</i>ELLE says Ollama is not running</div>
    <div class="collapsible-body">
      <p>Check if Ollama is installed and running:</p>
      <pre><code># Check status
systemctl status ollama

# Start Ollama
sudo systemctl start ollama

# Enable auto-start
sudo systemctl enable ollama</code></pre>
    </div>
  </li>
  <li>
    <div class="collapsible-header"><i class="material-icons">error_outline</i>Permission denied errors</div>
    <div class="collapsible-body">
      <p>The <code>elled</code> daemon runs as the <code>elle</code> user. Ensure the service is running:</p>
      <pre><code>sudo systemctl restart elled
journalctl -u elled -f  # View logs</code></pre>
      <p>If using "Convenient" privilege mode, ensure you've logged out and back in for group membership to take effect.</p>
    </div>
  </li>
  <li>
    <div class="collapsible-header"><i class="material-icons">error_outline</i>Models not loading</div>
    <div class="collapsible-body">
      <p>Verify models are downloaded:</p>
      <pre><code>ollama list</code></pre>
      <p>If models are missing, pull them again:</p>
      <pre><code>ollama pull phi3.5:3.8b-mini-instruct-q8_0
ollama pull qwen2.5:7b-instruct-q8_0</code></pre>
    </div>
  </li>
  <li>
    <div class="collapsible-header"><i class="material-icons">error_outline</i>Polkit authentication not working</div>
    <div class="collapsible-body">
      <p>If you chose "Convenient" or "Passwordless" mode but still get password prompts:</p>
      <pre><code># Check if rules file exists
ls -la /etc/polkit-1/rules.d/50-elle.rules

# Check group membership (for Convenient mode)
groups

# If 'elle' not listed, re-run setup or add manually:
sudo usermod -aG elle $USER
# Then log out and back in</code></pre>
    </div>
  </li>
  <li>
    <div class="collapsible-header"><i class="material-icons">error_outline</i>Not enough memory</div>
    <div class="collapsible-body">
      <p>ELLE needs ~12GB RAM when both models are loaded. If you have less memory:</p>
      <ul>
        <li>Use a smaller generation model: <code>ollama pull qwen2.5:3b-instruct-q8_0</code></li>
        <li>Increase swap space</li>
        <li>Enable zram compression</li>
      </ul>
      <p>See <a href="{{ '/docs/hardware' | relative_url }}">Hardware Requirements</a> for details.</p>
    </div>
  </li>
</ul>

---

## Next Steps

Once ELLE is running:

1. **Ask a question:** `how much disk space is left?`
2. **Try a task:** `show me recent errors in the logs`
3. **Create a reactive function:** `/react create`
4. **Read the docs:** `/help` or [User Documentation]({{ '/docs' | relative_url }})

<div style="text-align: center; margin: 2rem 0;">
  <a href="{{ '/docs' | relative_url }}" class="btn btn-large waves-effect waves-light">
    <i class="material-icons left">menu_book</i>Read the Documentation
  </a>
</div>
