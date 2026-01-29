# elled-gpud - GPU Telemetry Daemon

GPU monitoring daemon for ELLE. Polls NVIDIA GPUs via NVML and emits telemetry
events to the telemetryd socket.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    elled-gpud (C)                        │
│  ┌────────────────────────────────────────────────────┐  │
│  │  NVML via dlopen (no build-time dependency)        │  │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐   │  │
│  │  │ Memory/Util │ │ Temp/Power  │ │ ECC/Throttle│   │  │
│  │  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘   │  │
│  │         └───────────────┴───────────────┘          │  │
│  │              Polling Loop (1s default)             │  │
│  └────────────────────────────────────────────────────┘  │
│         Connects to /run/elle/telemetry.sock             │
└──────────────────────────────────────────────────────────┘
                              │
                              v
┌──────────────────────────────────────────────────────────┐
│                  elled-telemetryd (C)                    │
│         Receives GPU events, routes to Python            │
└──────────────────────────────────────────────────────────┘
```

## Building

```bash
# Install dependencies
sudo apt install gcc libjansson-dev

# Build
make

# Check dependencies
make check-deps
```

## Usage

```bash
# Run with default settings
sudo ./build/elled-gpud

# Run with verbose logging
sudo ./build/elled-gpud -v

# Custom interval (500ms)
sudo ./build/elled-gpud -i 500

# Custom thresholds
sudo ./build/elled-gpud --temp-warning 75 --temp-critical 85

# Don't emit periodic status events (only warnings/errors)
sudo ./build/elled-gpud --no-status
```

## Event Types

| Event | Trigger | Severity |
|-------|---------|----------|
| `gpu_memory_pressure` | VRAM > 90% | warning |
| `gpu_memory_critical` | VRAM > 95% | critical |
| `gpu_thermal_warning` | Temp > 80°C | warning |
| `gpu_thermal_critical` | Temp > 90°C | critical |
| `gpu_throttling` | Throttle reasons != 0 | warning |
| `gpu_ecc_error` | ECC error count > 0 | error |
| `gpu_status` | Every poll (optional) | info |

## Event Format (NDJSON)

```json
{
  "ts": 1706500000000000000,
  "source": "gpu",
  "severity": "warning",
  "category": "gpu",
  "message": "GPU 0 (RTX 4090) memory at 94% (22.5/24.0 GB)",
  "entity": "gpu:0",
  "fingerprint": "a1b2c3d4e5f6g7h8",
  "raw": {
    "gpu_index": 0,
    "gpu_name": "NVIDIA RTX 4090",
    "gpu_uuid": "GPU-abc123...",
    "mem_used_mb": 23040,
    "mem_total_mb": 24576,
    "mem_pct": 94,
    "util_pct": 87,
    "temp_c": 72,
    "power_w": 320,
    "throttle_reasons": 0,
    "ecc_errors": 0,
    "processes": [
      {"pid": 1234, "name": "python", "mem_mb": 18000}
    ]
  }
}
```

## No GPU Behavior

If no NVIDIA GPU is detected, gpud exits with code 0 (not an error).
This is the expected behavior on systems without NVIDIA GPUs.

## Systemd Service

See `ebpf/packaging/elled-gpud.service` for the systemd unit file.
