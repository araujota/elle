---
layout: default
title: Hardware Requirements
---

# Hardware Requirements

## Minimum Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| RAM | 8 GB | 16 GB |
| Storage | 10 GB | 20 GB |
| CPU | 4 cores | 8 cores |
| Kernel | 5.15+ | 6.5+ |

## Memory Usage

### ELLE Components

| Component | Memory |
|-----------|--------|
| elled daemon | 50-150 MB |
| elle CLI | 30-80 MB |
| SQLite databases | 10-50 MB |

### Ollama Models

| Model | Memory |
|-------|--------|
| phi3.5:3.8b (classifier) | ~4 GB |
| qwen2.5:7b (generator) | ~8 GB |
| **Peak usage** | **~12 GB** |

The classification model stays loaded permanently. The generation model unloads after 10 minutes of inactivity.

## Storage Breakdown

| Component | Size |
|-----------|------|
| ELLE package | ~5 MB |
| Python dependencies | ~50 MB |
| Man Vault index | ~100 MB |
| Ollama models | ~12 GB |
| **Total** | **~15 GB** |

## GPU Acceleration

ELLE uses Ollama, which supports GPU acceleration for faster inference.

### NVIDIA

```bash
# Install drivers
sudo apt install nvidia-driver-535

# Verify
nvidia-smi
```

Ollama automatically detects NVIDIA GPUs.

| GPU | VRAM | Notes |
|-----|------|-------|
| GTX 1660 | 6 GB | Minimum for 7B models |
| RTX 3060 | 12 GB | Recommended |
| RTX 3080+ | 16+ GB | Optimal |

### CPU-Only

ELLE works without GPU acceleration. Inference is slower but fully functional.

## Kernel Requirements

| Version | Features |
|---------|----------|
| 5.15+ | Basic functionality |
| 5.8+ | eBPF telemetry (CAP_BPF) |
| 6.5+ | Best performance |

## Supported Platforms

### Primary

- Ubuntu 24.04 LTS (Noble)
- Ubuntu 22.04 LTS (Jammy)

### Secondary

- Debian 12 (Bookworm)
- Linux Mint 21+

### Not Supported

- Non-systemd distributions
- Kernel < 5.15
- Non-x86_64 architectures
