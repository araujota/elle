# ELLE Hardware Requirements

This document describes the hardware requirements for running ELLE.

## Minimum Requirements

| Component | Requirement |
|-----------|-------------|
| RAM | 8 GB |
| Storage | 10 GB available |
| CPU | 4 cores |
| Kernel | 5.15+ (Ubuntu 22.04+) |

These minimums allow ELLE to run with smaller LLM models (3B-7B parameters)
with reduced performance. Context windows will be limited.

## Recommended Requirements

| Component | Requirement |
|-----------|-------------|
| RAM | 16 GB |
| Storage | 20 GB available |
| CPU | 8 cores |
| Kernel | 6.5+ (Ubuntu 24.04) |

Recommended configuration provides comfortable headroom for:
- Larger context windows (32K tokens)
- Concurrent operations
- Man Vault semantic search
- eBPF telemetry collection

## Memory Usage Breakdown

### ELLE Components

| Component | Memory |
|-----------|--------|
| elled daemon | 50-150 MB |
| elle CLI | 30-80 MB |
| SQLite databases | 10-50 MB |
| eBPF programs | 5-20 MB |

### Ollama LLM Models

| Model | Memory (Quantized) |
|-------|-------------------|
| phi3.5:3.8b-mini-instruct-q8_0 | ~4 GB |
| qwen2.5:7b-instruct-q8_0 | ~8 GB |
| Both loaded | ~12 GB peak |

**Important:** Ollama keeps models in memory. The SLM (classification model)
is kept loaded permanently (`keep_alive=-1`) while the LLM (generation model)
unloads after 10 minutes of inactivity.

## Storage Requirements

### Package Installation

| Component | Size |
|-----------|------|
| ELLE package | ~5 MB |
| Python dependencies | ~50 MB |
| Man Vault (indexed) | ~100 MB |
| Incident Vault (grows over time) | Variable |

### Ollama Models

| Model | Size on Disk |
|-------|--------------|
| phi3.5:3.8b-mini-instruct-q8_0 | ~4 GB |
| qwen2.5:7b-instruct-q8_0 | ~8 GB |

**Total initial storage:** ~15 GB (with both models)

## GPU Acceleration (Optional)

ELLE uses Ollama for inference, which supports GPU acceleration.

### NVIDIA CUDA

Recommended for significantly faster inference:

| GPU | VRAM | Notes |
|-----|------|-------|
| GTX 1660 | 6 GB | Minimum for 7B models |
| RTX 3060 | 12 GB | Recommended for 7B models |
| RTX 3080+ | 16+ GB | Optimal performance |

Install NVIDIA driver and CUDA toolkit:
```bash
sudo apt install nvidia-driver-535 nvidia-cuda-toolkit
```

Ollama automatically detects and uses NVIDIA GPUs.

### AMD ROCm

Experimental support via Ollama:
```bash
# Requires ROCm-compatible GPU and drivers
ollama serve --rocm
```

### CPU-Only Mode

ELLE works without GPU acceleration using CPU inference.
Performance is slower but functional for all operations.

## Kernel Requirements

### Minimum (5.15+)

Basic functionality works on Ubuntu 22.04 LTS kernel:
- Systemd journal monitoring
- File system probes
- Network diagnostics

### Recommended (5.8+)

Required for eBPF telemetry with unprivileged BPF:
- CAP_BPF capability (added in 5.8)
- CAP_PERFMON capability (added in 5.8)
- Enhanced tracepoint access

### Optimal (6.5+)

Ubuntu 24.04 LTS kernel provides:
- Best eBPF performance
- Improved BPF verifier
- Additional tracepoints

## eBPF Telemetry Requirements

For full eBPF telemetry collection:

1. **Kernel version:** 5.8+ (ideally 6.5+)
2. **Capabilities:** CAP_BPF, CAP_PERFMON, CAP_NET_ADMIN
3. **BPF filesystem:** Mounted at /sys/fs/bpf
4. **Python BCC:** `python3-bcc` package installed

eBPF is optional. ELLE falls back to journal/probe monitoring without it.

## Supported Platforms

### Primary Support

| Platform | Status |
|----------|--------|
| Ubuntu 24.04 LTS (Noble) | Full support |
| Ubuntu 22.04 LTS (Jammy) | Supported |

### Secondary Support

| Platform | Status |
|----------|--------|
| Debian 12 (Bookworm) | Should work |
| Linux Mint 21+ | Should work |

### Not Supported

- Non-systemd distributions
- Kernel versions < 5.15
- Non-x86_64 architectures (currently)

## Performance Tuning

### Memory Optimization

For systems with limited RAM:

1. Use smaller models:
   ```bash
   ollama pull phi3.5:3.8b-mini-instruct-q4_0  # Lower quantization
   ```

2. Reduce daemon memory limit:
   ```bash
   sudo systemctl edit elled
   # Add: MemoryMax=256M
   ```

3. Disable eBPF (if not needed):
   ```toml
   # /etc/elle/elle.toml
   [daemon]
   ebpf_enabled = false
   ```

### Storage Optimization

For systems with limited storage:

1. Use only the SLM (skip 7B model for generation)
2. Periodically prune incident vault
3. Limit Man Vault to essential commands

### Network Considerations

ELLE is local-first and requires no internet for operation.
However, initial setup requires:

1. Package installation (apt repositories)
2. Ollama model downloads (~12 GB total)

After installation, ELLE operates fully offline.

## Verification

Check your system meets requirements:

```bash
# Memory
free -h

# CPU cores
nproc

# Disk space
df -h /var/lib

# Kernel version
uname -r

# Check for GPU
lspci | grep -i nvidia

# Check Ollama
ollama list
```
