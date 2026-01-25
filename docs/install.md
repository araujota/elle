---
layout: default
title: Installation
---

# Installation

## From APT Repository (Recommended)

The easiest way to install ELLE is from our APT repository.

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

## From Source

For development or if you prefer building from source:

### Prerequisites

```bash
sudo apt install python3.11 python3.11-venv python3-pip git
```

### Clone and Install

```bash
git clone https://github.com/araujota/elle.git
cd elle
python3 -m venv .venv
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

## Verify Installation

After installation, verify everything is working:

```bash
# Check ELLE version
elle --version

# Check daemon status
systemctl status elled

# Check Ollama
ollama list

# Run ELLE
elle
```

On first launch, ELLE will guide you through a setup wizard to configure your preferences.

## Uninstall

```bash
# Remove ELLE
sudo apt purge elle

# This removes:
# - The elle and elled binaries
# - Configuration in /etc/elle/
# - State data in /var/lib/elle/
# - The elle system user
```

To keep your data, use `sudo apt remove elle` instead of `purge`.

## Troubleshooting

### ELLE says Ollama is not running

```bash
# Check Ollama status
systemctl status ollama

# Start Ollama
sudo systemctl start ollama
```

### Permission denied errors

The `elled` daemon runs as the `elle` user. Ensure the service is running:

```bash
sudo systemctl restart elled
journalctl -u elled -f  # View logs
```

### Models not loading

Verify models are downloaded:

```bash
ollama list
```

If models are missing, pull them again:

```bash
ollama pull phi3.5:3.8b-mini-instruct-q8_0
ollama pull qwen2.5:7b-instruct-q8_0
```
