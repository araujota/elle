# elled-telemetryd Protocol Specification

## Overview

`elled-telemetryd` is a unified C daemon that collects, normalizes, and emits
telemetry events from multiple sources. It communicates with the Python `elled`
daemon via a Unix socket using newline-delimited JSON (NDJSON).

The key benefit is that events arrive **pre-normalized** - they already have
category, entity, and fingerprint set, eliminating the need for Python-side
processing.

## Socket Path

Default: `/run/elle/telemetry.sock`

The daemon creates a Unix domain socket at this path and accepts multiple
concurrent client connections. Each client receives the same stream of events.

## Event Format

Events are emitted as NDJSON (one JSON object per line). Each event has the
following structure:

```json
{
  "ts": 1706300000000000000,
  "source": "journal",
  "severity": "warning",
  "category": "oom",
  "message": "OOM killer invoked, killed process nginx (PID 1234)",
  "entity": "process:nginx",
  "fingerprint": "a1b2c3d4e5f67890",
  "raw": { ... }
}
```

### Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `ts` | uint64 | Timestamp in nanoseconds since Unix epoch |
| `source` | string | Event source (see Source Types) |
| `severity` | string | Severity level (see Severity Levels) |
| `category` | string | Event category (see Categories) |
| `message` | string | Human-readable event description |
| `entity` | string? | Entity identifier (may be null) |
| `fingerprint` | string | SHA256[:16] hex for deduplication |
| `raw` | object? | Source-specific raw data (optional) |

### Source Types

| Source | Description |
|--------|-------------|
| `journal` | Journald messages via sd-journal API |
| `kernel` | Kernel messages (transport=kernel) |
| `probe` | Periodic polling probes |
| `ebpf` | eBPF tracepoint/kprobe events |
| `docker` | Docker container events |
| `inotify` | File system change events |

### Severity Levels

| Severity | journald Priority | Description |
|----------|------------------|-------------|
| `debug` | 7 | Debug-level messages |
| `info` | 6 | Informational messages |
| `notice` | 5 | Normal but significant conditions |
| `warning` | 4 | Warning conditions |
| `error` | 3 | Error conditions |
| `critical` | 0-2 | Critical/alert/emergency conditions |

### Categories

| Category | Description |
|----------|-------------|
| `oom` | Out-of-memory events |
| `disk` | Disk/storage issues (I/O errors, space) |
| `net` | Network issues (retransmits, drops) |
| `auth` | Authentication/permission issues |
| `service` | Systemd service events |
| `thermal` | Temperature warnings |
| `smart` | SMART disk health |
| `fs` | Filesystem events (mounts, changes) |
| `pkg` | Package management events |
| `docker` | Container lifecycle events |
| `kernel_panic` | Kernel panics/oops |
| `other` | Uncategorized events |

### Entity Format

Entities follow the format `type:identifier`:

| Pattern | Description |
|---------|-------------|
| `service:nginx` | Systemd service |
| `process:nginx` | Process by name |
| `container:web-1` | Docker container |
| `interface:eth0` | Network interface |
| `device:sda` | Block device |
| `file:/etc/passwd` | Filesystem path |
| `mount:/var` | Mount point |
| `connection:1.2.3.4:443` | Network connection |
| `resource:memory` | System resource |

## Deduplication

The daemon maintains a deduplication window (default: 60 seconds) using fingerprints.
Events with the same fingerprint within the window are suppressed.

Fingerprints are computed as:
```
SHA256(category + ":" + entity + ":" + message)[:16]
```

## Graceful Degradation

The Python `elled` daemon implements graceful degradation:

1. On startup, check if `/run/elle/telemetry.sock` exists and is connectable
2. If yes: Start `TelemetrydWatcher` to receive pre-normalized events
3. If no: Fall back to Python-based watchers (journal, kernel, eBPF, probes)

This allows the system to operate in environments where the C daemon is not
installed or is unavailable.

## Connection Handling

- The daemon accepts multiple simultaneous client connections
- Each client receives all events (broadcast model)
- Disconnected clients are automatically cleaned up
- Clients should implement reconnection with exponential backoff

### Recommended Reconnection Strategy

```python
reconnect_delay = 2.0  # Initial delay
max_delay = 60.0       # Maximum delay

while not shutdown:
    try:
        await connect()
        reconnect_delay = 2.0  # Reset on success
        await read_events()
    except ConnectionError:
        await sleep(reconnect_delay)
        reconnect_delay = min(reconnect_delay * 2, max_delay)
```

## Memory Budget

The daemon is designed to use less than 20MB of memory:

| Component | Budget |
|-----------|--------|
| Base process | 2MB |
| PCRE2 patterns | 1MB |
| Dedup ring buffer | 2MB |
| Journal watcher | 50KB |
| Docker watcher | 100KB |
| Inotify watcher | 50KB |
| Probe scheduler | 1MB |
| eBPF loader (optional) | 8MB |
| Socket buffers | 500KB |
| **Total** | ~15MB |

## Configuration

Configuration is set via command-line arguments:

```
elled-telemetryd [OPTIONS]

Options:
  -s, --socket PATH    Socket path (default: /run/elle/telemetry.sock)
  -d, --dedupe SEC     Deduplication window in seconds (default: 60)
  -v, --verbose        Increase verbosity (can be repeated)
  -h, --help           Show help message
```

## Example Client (Python)

```python
import asyncio
import json
from pathlib import Path

SOCKET = Path("/run/elle/telemetry.sock")

async def watch_events():
    reader, writer = await asyncio.open_unix_connection(str(SOCKET))

    try:
        while True:
            line = await reader.readline()
            if not line:
                break  # Connection closed

            event = json.loads(line.decode("utf-8"))
            print(f"[{event['severity']}] {event['category']}: {event['message']}")
    finally:
        writer.close()
        await writer.wait_closed()

asyncio.run(watch_events())
```

## Building

```bash
# Without eBPF support
cd ebpf/telemetryd
make

# With eBPF support
make ENABLE_EBPF=1

# Or using the convenience target
make ebpf
```

## Systemd Integration

The daemon is managed via systemd:

```bash
# Enable and start
systemctl enable elled-telemetryd
systemctl start elled-telemetryd

# Check status
systemctl status elled-telemetryd

# View logs
journalctl -u elled-telemetryd -f
```

The systemd unit file includes:
- Automatic restart on failure
- Memory limits (32MB max)
- CPU priority (nice 5)
- Security hardening (NoNewPrivileges, ProtectSystem, etc.)
- Conflicts with legacy daemons (elled-ebpfd, elled-probed)
