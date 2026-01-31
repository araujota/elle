# Installation

## Hardware requirements

### Minimum

| Component | Requirement |
|-----------|-------------|
| RAM | 8 GB |
| Storage | 10 GB available |
| CPU | 4 cores |
| Kernel | 5.15+ (Ubuntu 22.04+) |

These minimums allow ELLE to run with smaller LLM models (3B-7B parameters) with reduced performance.

### Recommended

| Component | Requirement |
|-----------|-------------|
| RAM | 16 GB |
| Storage | 20 GB available |
| CPU | 8 cores |
| Kernel | 6.5+ (Ubuntu 24.04) |

Recommended configuration provides headroom for 32K-token context windows, concurrent operations, Man Vault semantic search, and eBPF telemetry.

### Memory breakdown

| Component | Memory |
|-----------|--------|
| `elled` daemon | 50-150 MB |
| `elle` CLI | 30-80 MB |
| PostgreSQL | 100-256 MB |
| LLM model (`qwen2.5:7b-instruct-q8_0`) | ~8 GB |

The LLM stays loaded permanently in GPU/CPU memory (`keep_alive=-1`). The daemon sends periodic warmup pings every 5 minutes to ensure fast response times.

### GPU acceleration (optional)

ELLE uses Ollama for inference, which supports GPU acceleration:

| GPU | VRAM | Notes |
|-----|------|-------|
| GTX 1660 | 6 GB | Minimum for 7B models |
| RTX 3060 | 12 GB | Recommended for 7B models |
| RTX 3080+ | 16+ GB | Optimal performance |

```bash
# Install NVIDIA driver and CUDA toolkit
sudo apt install nvidia-driver-535 nvidia-cuda-toolkit
```

Ollama automatically detects NVIDIA GPUs. CPU-only mode works but is slower.

## Supported platforms

| Platform | Status |
|----------|--------|
| Ubuntu 24.04 LTS (Noble) | Full support |
| Ubuntu 22.04 LTS (Jammy) | Supported |
| Debian 12 (Bookworm) | Should work |
| Linux Mint 21+ | Should work |

Not supported: non-systemd distributions, kernel < 5.15, non-x86_64 architectures.

## Prerequisites

- **PostgreSQL** — ELLE uses PostgreSQL for all persistent storage
- **Ollama** — Local LLM inference engine

## Install via APT (recommended)

### 1. Add the repository

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
```

### 2. Install ELLE

```bash
sudo apt update
sudo apt install elle
```

## Install from source (development)

### 1. Install system dependencies

```bash
sudo apt install python3 python3-venv python3-pip git libaugeas-dev
```

### 2. Clone and install

```bash
git clone https://github.com/araujota/elle.git
cd elle
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## PostgreSQL setup

ELLE stores telemetry, incidents, man pages, capabilities, reactive functions, and policy rules in PostgreSQL.

```bash
# Install PostgreSQL
sudo apt install postgresql

# Provision the database
elle setup database
```

This creates the `elle` database and user with the required schemas.

## Ollama setup

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull the LLM model (~8 GB)
ollama pull qwen2.5:7b-instruct-q8_0
```

See [[LLM Providers|LLM-Providers]] for model details, fallback chains, and OpenAI-compatible provider configuration.

## Start ELLE

```bash
# Start the daemon
sudo systemctl start elled
sudo systemctl enable elled  # Optional: start on boot

# Launch ELLE
elle
```

On first launch, the [[Setup Wizard|Setup-Wizard]] guides you through configuration.

## Verify installation

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

## Uninstall

```bash
# Remove ELLE (keeps config and data)
sudo apt remove elle

# Remove ELLE and all data
sudo apt purge elle
```

Purge removes `/etc/elle/`, `/var/lib/elle/`, and the `elle` system user.

## eBPF telemetry requirements

For full eBPF telemetry (optional):

1. Kernel 5.8+ (ideally 6.5+)
2. Capabilities: `CAP_BPF`, `CAP_PERFMON`, `CAP_NET_ADMIN`
3. BPF filesystem mounted at `/sys/fs/bpf`

eBPF is optional. ELLE falls back to journal/probe monitoring without it.

## Performance tuning

### Low memory systems

```bash
# Use a smaller model (~4 GB)
ollama pull qwen2.5:3b-instruct-q8_0

# Reduce daemon memory limit
sudo systemctl edit elled
# Add: MemoryMax=256M

# Disable eBPF if not needed
# Set ebpf_enabled = false in /etc/elle/elle.toml
```

### Network considerations

ELLE is local-first and requires no internet for operation after initial setup. Initial setup requires package installation and Ollama model downloads (~8 GB).
