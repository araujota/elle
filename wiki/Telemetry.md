# Telemetry

ELLE monitors your system through a two-stage pipeline: a C daemon (`elled-telemetryd`) handles high-performance event capture and normalization, then sends structured events to the Python daemon (`elled`) for correlation and incident creation.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│               elled-telemetryd (C Daemon)              │
│                                                        │
│  Watchers:  Journal | Docker | Inotify | eBPF Probes   │
│  Probes:    PSI | Memory | Disk | Network | Thermal    │
│             Package | Port                              │
│                                                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │              C Normalizer (PCRE2)                  │  │
│  │  • 40+ category detection patterns                 │  │
│  │  • 23 entity extraction patterns                   │  │
│  │  • SHA256 fingerprinting                           │  │
│  │  • 60-second deduplication window                  │  │
│  └──────────────────────────────────────────────────┘  │
│                          │                              │
│                          ▼                              │
│               /run/elle/telemetry.sock                  │
│                    (NDJSON output)                      │
└──────────────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────┐
│                   elled (Python Daemon)                 │
│                                                        │
│  TelemetrydWatcher → TelemetryEvent → IncidentCorrelator│
│                                                        │
└──────────────────────────────────────────────────────┘
```

## Event sources

### Journal watcher

Monitors all systemd journal entries in real-time via `sd-journal` API.

Captured fields: `MESSAGE`, `PRIORITY` (0-7), `SYSLOG_IDENTIFIER`, `_SYSTEMD_UNIT`, `_COMM`, `_EXE`, `_TRANSPORT`, `__REALTIME_TIMESTAMP`.

### Docker watcher

Monitors container lifecycle via `docker events --format json`.

| Signal | Description |
|--------|-------------|
| `start`, `stop`, `die`, `restart`, `kill`, `oom` | Standard Docker events |
| `crashloop` | Synthetic: 3+ restarts in 5 minutes |

### Inotify watcher

Monitors security-sensitive files for modifications, creations, deletions, and attribute changes.

Default watched paths:
- `/root/.ssh/authorized_keys`
- `/home/*/.ssh/authorized_keys`
- `/etc/ssh/sshd_config`
- `/etc/sudoers`
- `/etc/passwd`
- `/etc/shadow`

Generates unified diffs for text file changes.

### eBPF probes

Kernel-level tracing via BPF programs attached to tracepoints. Requires kernel 5.8+ and `CAP_BPF`/`CAP_PERFMON` capabilities. Optional — ELLE works without eBPF.

| Probe | Tracepoint | What it captures |
|-------|-----------|-----------------|
| **OOM Kill** | `oom:mark_victim` | Process ID, command, memory stats |
| **Process Execution** | `syscalls:sys_enter_execve` | PID, executable, exit code, latency |
| **Block I/O** | `block:block_rq_issue/complete` | Device, sector, bytes, latency, errors |
| **TCP Retransmit** | `tcp:tcp_retransmit_skb` | Source/dest IP and port, TCP state |
| **Capability Denial** | `capability:cap_capable` | Process, capability name, UID |
| **File Permission** | `syscalls:sys_enter_openat` | Filename, flags, error (EACCES/EPERM) |
| **Mount Operations** | `syscalls:sys_enter_mount` | Source, target, fstype, remount-ro detection |

### Periodic probes

| Probe | Source | Default interval | Key metrics |
|-------|--------|-----------------|-------------|
| **PSI** | `/proc/pressure/*` | 30s | CPU, memory, I/O pressure (10s/60s/5min averages) |
| **Memory** | `/proc/meminfo` | 30s | Total, available, free, swap usage. Warning at 85% |
| **Disk** | `/proc/mounts` + `statvfs()` | 5 min | Per-mount usage. Warning at 90%. Excludes tmpfs, squashfs, etc. |
| **Network** | `/sys/class/net/*/` | 60s | Interface state, rx/tx bytes, errors, drops. Alerts on state change or >10 errors |
| **Thermal** | `/sys/class/thermal/*/temp` | 60s | Per-zone temperature. Warning at 80°C |
| **Package** | `/var/lib/dpkg/status` | 5 min | Install, upgrade, remove events. Checks mtime before re-parsing |
| **Port** | `/proc/net/tcp*`, `/proc/net/udp*` | 60s | Listening ports, associated PIDs. Monitors sensitive ports (22, 80, 443, 3306, 5432, 6379, 27017) |

## Category detection

The C normalizer uses 40+ PCRE2 patterns to categorize events:

| Category | Pattern count | Examples |
|----------|--------------|---------|
| `kernel_panic` | 8 | `Kernel panic`, `BUG:`, `Oops:`, `stack overflow` |
| `oom` | 2 | `Out of memory`, `oom-kill` |
| `disk` | 2 | `I/O error`, `disk quota exceeded` |
| `smart` | 1 | `SMART.*error`, `media.*error` |
| `fs` | 2 | `EXT4-fs error`, `Remounting filesystem read-only` |
| `net` | 10 | `link down/up`, `connection refused`, `DNS.*fail`, `TCP.*retransmit` |
| `auth` | 7 | `authentication fail`, `permission denied`, `Failed password` |
| `service` | 4 | `Starting/Started/Stopped/Failed`, `entered failed state` |
| `thermal` | 2 | `temperature.*critical`, `thermal.*throttl` |
| `pkg` | 3 | `install/remove/upgrade.*package` |
| `docker` | 4 | `container.*start/stop/die`, `crashloop.*detected` |

## Entity extraction

The normalizer extracts 23 entity types from messages:

| Entity | Example match | Extracted value |
|--------|--------------|----------------|
| `service:X` | `nginx.service started` | `service:nginx` |
| `interface:X` | `eth0 link down` | `interface:eth0` |
| `disk:X` | `error on sda` | `disk:sda` |
| `container:X` | `container abc123 died` | `container:abc123` |
| `mount:X` | `mount /home failed` | `mount:/home` |
| `user:X` | `user john failed login` | `user:john` |
| `pid:N` | `killed process 1234` | `pid:1234` |
| `port:N` | `port 8080 in use` | `port:8080` |
| `file:X` | `error reading /etc/foo` | `file:/etc/foo` |
| `module:X` | `module foo loaded` | `module:foo` |
| `cpu:N` | `CPU 3 stuck` | `cpu:3` |

## TelemetryEvent format

Events are emitted as NDJSON over Unix socket at `/run/elle/telemetry.sock`:

```json
{
  "ts": 1706300000000000000,
  "source": "journal|kernel|docker|inotify|probe|ebpf",
  "severity": "debug|info|notice|warning|error|critical",
  "category": "oom|disk|net|auth|service|docker|fs|pkg|thermal|smart|kernel_panic|other",
  "message": "Human-readable event description",
  "entity": "service:nginx|interface:eth0|container:abc123|null",
  "fingerprint": "sha256_16char_hex",
  "raw": { }
}
```

## Fingerprinting

Each event gets a SHA256-based fingerprint (truncated to 16 hex characters) for deduplication. Events with the same fingerprint within the 60-second dedup window are suppressed.

Incident-level fingerprints are 31-dimensional vectors used for similarity search across the Incident Vault.

## Socket protocol

- **Path:** `/run/elle/telemetry.sock`
- **Format:** NDJSON (newline-delimited JSON)
- **Type:** Stream socket, multiple concurrent clients supported
