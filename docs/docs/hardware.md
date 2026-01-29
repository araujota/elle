---
layout: default
title: Hardware Requirements
---

# Hardware Requirements

System specifications for running ELLE.

---

## Quick Reference

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **RAM** | 8 GB | 16 GB |
| **Storage** | 15 GB | 25 GB |
| **CPU** | 4 cores | 8 cores |
| **Kernel** | 5.15+ | 6.5+ |
| **GPU** | Not required | NVIDIA (CUDA) |

---

## Memory Usage

### ELLE Components

| Component | Memory |
|-----------|--------|
| `elled` daemon | 50-150 MB |
| `elle` CLI | 30-80 MB |
| SQLite databases | 10-50 MB |
| Reactive Engine | 20-50 MB |

### Ollama Models

| Model | Memory | Purpose |
|-------|--------|---------|
| `qwen2.5:7b-instruct-q8_0` | ~8 GB | LLM for reasoning (kept warm for local inference) |

<div class="callout callout-info">
<strong>Local Inference</strong><br>
For local inference, the LLM stays loaded in GPU memory permanently for fast response times.
The daemon sends periodic warmup pings to ensure the model remains ready.
Intent routing uses rule-based classification (no LLM required).
</div>

### Low Memory Options

If you have less than 16GB RAM:

```bash
# Use a smaller generation model (~4GB instead of ~8GB)
ollama pull qwen2.5:3b-instruct-q8_0

# Or use llama3.2:3b (~3GB)
ollama pull llama3.2:3b-instruct-q8_0
```

You can also:
- Increase swap space
- Enable zram compression
- Disable the generation model keepalive in config

---

## Storage Breakdown

| Component | Size |
|-----------|------|
| ELLE package | ~5 MB |
| Python dependencies | ~50 MB |
| Man Vault index | ~100 MB |
| Incident Vault | ~10 MB (grows over time) |
| Reactive functions DB | ~5 MB |
| Ollama models | ~12 GB |
| **Total** | **~15 GB** |

<div class="callout callout-warning">
<strong>Model Storage</strong><br>
Ollama models are stored in <code>~/.ollama/models/</code> and can be shared across users.
</div>

---

## GPU Acceleration

ELLE uses Ollama, which supports GPU acceleration for faster inference.

### NVIDIA GPUs

```bash
# Install drivers (Ubuntu)
sudo apt install nvidia-driver-535

# Verify installation
nvidia-smi

# Ollama automatically detects NVIDIA GPUs
```

| GPU | VRAM | Notes |
|-----|------|-------|
| GTX 1660 | 6 GB | Minimum for 7B models |
| RTX 3060 | 12 GB | Recommended |
| RTX 3080+ | 16+ GB | Optimal |
| RTX 4090 | 24 GB | Fastest inference |

### AMD GPUs (ROCm)

Experimental support via Ollama's ROCm build:

```bash
# See Ollama docs for ROCm installation
# https://ollama.ai/docs/gpu
```

### CPU-Only

ELLE works without GPU acceleration. Inference is slower but fully functional.

| Hardware | 7B Model Speed | Notes |
|----------|----------------|-------|
| i5-10400 | ~10 tokens/sec | Usable |
| i7-12700 | ~20 tokens/sec | Good |
| Ryzen 5800X | ~25 tokens/sec | Good |
| Apple M1 | ~30 tokens/sec | Uses Metal |
| Apple M2 Pro | ~50 tokens/sec | Uses Metal |

---

## Kernel Requirements

| Version | Features |
|---------|----------|
| **5.15+** | Basic functionality |
| **5.8+** | eBPF telemetry (CAP_BPF) |
| **6.5+** | Best performance, io_uring |

### eBPF Requirements

For advanced telemetry (syscall tracing, network monitoring):

- Kernel 5.8+ with BPF support
- `CAP_BPF` capability for the `elled` daemon
- BCC tools installed (`sudo apt install bpfcc-tools`)

eBPF is optional and disabled by default. Enable in setup wizard or config.

---

## Supported Platforms

### Primary (Fully Tested)

<div class="row">
  <div class="col s12 m6">
    <div class="card">
      <div class="card-content">
        <span class="card-title"><i class="material-icons left">check_circle</i>Ubuntu 24.04 LTS</span>
        <p>Noble Numbat — Primary target platform</p>
        <ul>
          <li>Kernel 6.5+</li>
          <li>Python 3.12</li>
          <li>Full eBPF support</li>
        </ul>
      </div>
    </div>
  </div>
  <div class="col s12 m6">
    <div class="card">
      <div class="card-content">
        <span class="card-title"><i class="material-icons left">check_circle</i>Ubuntu 22.04 LTS</span>
        <p>Jammy Jellyfish — Well supported</p>
        <ul>
          <li>Kernel 5.15+</li>
          <li>Python 3.10+</li>
          <li>Basic eBPF support</li>
        </ul>
      </div>
    </div>
  </div>
</div>

### Secondary (Community Tested)

| Platform | Status | Notes |
|----------|--------|-------|
| Debian 12 (Bookworm) | Works | May need backports for newer Python |
| Linux Mint 21+ | Works | Based on Ubuntu 22.04 |
| Pop!_OS 22.04+ | Works | Based on Ubuntu |

### Not Supported

| Platform | Reason |
|----------|--------|
| Non-systemd distributions | Requires systemd for journal monitoring |
| Kernel < 5.15 | Missing required features |
| Non-x86_64 architectures | Ollama binaries not available |
| WSL2 | Limited systemd support |
| macOS / Windows | Linux-only |

---

## Virtual Machines

ELLE works in VMs with these considerations:

### Recommended VM Settings

| Setting | Value |
|---------|-------|
| RAM | 16 GB+ |
| CPU | 4+ cores |
| Storage | 30 GB+ |
| Nested virtualization | Enabled (for Docker) |

### VM-Specific Notes

**VirtualBox:**
- Enable "Nested VT-x/AMD-V"
- Use VirtIO drivers for best performance

**VMware:**
- Enable "Virtualize Intel VT-x/EPT"
- Allocate sufficient RAM for Ollama

**KVM/QEMU:**
- Use `-cpu host` for best performance
- Consider GPU passthrough for acceleration

**Cloud VMs:**
- AWS: t3.xlarge or larger
- GCP: n2-standard-4 or larger
- Azure: Standard_D4s_v3 or larger

---

## Performance Tuning

### For Faster Inference

1. **Use GPU acceleration** — 5-10x faster than CPU
2. **Keep models warm** — Adjust `keep_alive` in Ollama config
3. **Use SSD storage** — Faster model loading
4. **More RAM** — Allows larger context windows

### For Lower Resource Usage

1. **Use smaller models** — 3B instead of 7B
2. **Reduce context window** — In ELLE config
3. **Disable unused telemetry** — In setup wizard
4. **Schedule heavy operations** — Use reactive functions for off-hours

### Config Example

```toml
# ~/.config/elle/elle.toml

[daemon]
# Reduce memory by disabling unused telemetry
ebpf_enabled = false
docker_enabled = false  # If not using Docker

[llm]
# Use smaller context for memory savings
context_size = 8192  # Default is 32768
```

---

## Troubleshooting

<ul class="collapsible">
  <li>
    <div class="collapsible-header"><i class="material-icons">error_outline</i>Out of memory errors</div>
    <div class="collapsible-body">
      <p>Check memory usage:</p>
      <pre><code>free -h
ollama ps</code></pre>
      <p>Solutions:</p>
      <ul>
        <li>Use a smaller model</li>
        <li>Add swap space: <code>sudo fallocate -l 8G /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile</code></li>
        <li>Enable zram: <code>sudo apt install zram-config</code></li>
      </ul>
    </div>
  </li>
  <li>
    <div class="collapsible-header"><i class="material-icons">error_outline</i>Slow inference</div>
    <div class="collapsible-body">
      <p>Check if GPU is being used:</p>
      <pre><code>nvidia-smi  # Should show Ollama process
ollama ps   # Shows loaded models</code></pre>
      <p>If GPU not detected:</p>
      <ul>
        <li>Ensure NVIDIA drivers are installed</li>
        <li>Restart Ollama: <code>sudo systemctl restart ollama</code></li>
        <li>Check CUDA: <code>nvcc --version</code></li>
      </ul>
    </div>
  </li>
  <li>
    <div class="collapsible-header"><i class="material-icons">error_outline</i>eBPF not working</div>
    <div class="collapsible-body">
      <p>Check kernel version:</p>
      <pre><code>uname -r  # Should be 5.8+</code></pre>
      <p>Check BPF support:</p>
      <pre><code>cat /boot/config-$(uname -r) | grep BPF</code></pre>
      <p>Install BCC tools:</p>
      <pre><code>sudo apt install bpfcc-tools linux-headers-$(uname -r)</code></pre>
    </div>
  </li>
</ul>

---

<div class="card">
  <div class="card-content">
    <span class="card-title"><i class="material-icons left" style="color: var(--sponsor-color);">favorite</i>Support ELLE</span>
    <p>Help us test on more platforms by sponsoring development.</p>
  </div>
  <div class="card-action">
    <a href="{{ site.github_sponsor }}" target="_blank"><i class="material-icons left">favorite</i>Sponsor on GitHub</a>
  </div>
</div>
