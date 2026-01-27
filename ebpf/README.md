# elled-ebpfd - ELLE eBPF Telemetry Collector

CO-RE (Compile Once - Run Everywhere) eBPF collector using libbpf.
Replaces the BCC-Python implementation with native C for improved performance.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        elled-ebpfd (C)                              │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  libbpf CO-RE Programs (BTF-aware)                          │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────┐ │   │
│  │  │   OOM    │ │  Exec    │ │ Block IO │ │ TCP Retransmit │ │   │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └───────┬────────┘ │   │
│  │       └────────────┴────────────┴───────────────┘          │   │
│  │                    BPF Ring Buffer (4MB)                    │   │
│  └────────────────────────────────────────────────────────────┘   │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │              Userspace (epoll event loop)                   │   │
│  │   Ring Buffer Consumer → NDJSON Serializer → Unix Socket   │   │
│  └────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│                     /run/elle/telemetry.sock                        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ NDJSON
                               v
┌─────────────────────────────────────────────────────────────────────┐
│                        elled (Python)                               │
│  UnixSocketWatcher → raw_queue → Normalizer → TelemetryEvent       │
└─────────────────────────────────────────────────────────────────────┘
```

## Requirements

### Build Dependencies

```bash
sudo apt-get install -y \
    clang llvm libbpf-dev bpftool \
    libelf-dev zlib1g-dev libjansson-dev \
    linux-tools-common
```

### Runtime Dependencies

- `libbpf1` - libbpf runtime
- `libjansson4` - JSON library
- Kernel with BTF (`/sys/kernel/btf/vmlinux`)
- Ubuntu 24.04 or newer recommended

## Building

```bash
cd ebpf

# Generate vmlinux.h from kernel BTF (one-time)
make vmlinux

# Build everything
make

# Run in test mode (stdout output)
sudo ./build/elled-ebpfd --stdout --verbose
```

## Installation

```bash
# Install binary
sudo make install

# Install systemd service
sudo cp packaging/elled-ebpfd.service /lib/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable elled-ebpfd
sudo systemctl start elled-ebpfd
```

## Usage

```
elled-ebpfd - ELLE eBPF Telemetry Collector

Usage: elled-ebpfd [options]

Options:
  --socket PATH    Unix socket path (default: /run/elle/telemetry.sock)
  --stdout         Output to stdout instead of socket
  --verbose        Enable verbose logging
  --no-oom         Disable OOM tracing
  --no-exec        Disable exec tracing
  --no-block       Disable block I/O tracing
  --no-tcp         Disable TCP retransmit tracing (default: disabled)
  --enable-tcp     Enable TCP retransmit tracing
  --help           Show this help
```

## Directory Structure

```
ebpf/
├── Makefile                      # Build orchestration
├── README.md                     # This file
├── bpf/
│   ├── elled.h                   # Shared event structures
│   ├── vmlinux.h                 # Generated from BTF (not in git)
│   ├── oom.bpf.c                 # OOM tracepoint
│   ├── exec.bpf.c                # execve enter/exit correlation
│   ├── block_io.bpf.c            # Block I/O latency tracking
│   └── tcp_retrans.bpf.c         # TCP retransmits
├── user/
│   ├── main.c                    # Entry point, signal handling
│   ├── collector.c/h             # Event loop, ring buffer polling
│   ├── socket.c/h                # Unix socket management
│   ├── json.c/h                  # NDJSON serialization (jansson)
│   └── config.c/h                # Runtime config
├── packaging/
│   └── elled-ebpfd.service       # systemd unit
└── tests/
    └── integration/              # Python integration tests
```

## Event Types

| Event | Tracepoint | Description |
|-------|------------|-------------|
| `oom_kill` | `oom:mark_victim` | OOM killer invoked |
| `exec_exit` | `syscalls:sys_exit_execve` | Process execution |
| `process_exit` | `sched:sched_process_exit` | Process death by signal |
| `block_rq_complete` | `block:block_rq_complete` | Block I/O completion |
| `net_retransmit` | `tcp:tcp_retransmit_skb` | TCP retransmission |

## JSON Output Format

Events are serialized as NDJSON (one JSON object per line):

```json
{
  "_SOURCE": "ebpf",
  "_EBPF_EVENT_TYPE": "oom_kill",
  "_EBPF_CATEGORY": "oom",
  "_EBPF_TS_NS": 1234567890,
  "_EBPF_CPU": 0,
  "MESSAGE": "OOM killer invoked, killed process python (PID 1234)",
  "PRIORITY": "2",
  "_PID": "1234",
  "_TGID": "1234",
  "_UID": "1000",
  "_COMM": "python",
  "_EBPF_OOM_SCORE_ADJ": "0",
  "_EBPF_TOTAL_VM": "1048576"
}
```

## Testing

### OOM Test

```bash
# Terminal 1: Run collector
sudo ./build/elled-ebpfd --stdout

# Terminal 2: Trigger OOM
stress --vm 1 --vm-bytes 95% --timeout 10s

# Verify JSON output contains oom_kill event
```

### Integration Test

```bash
# Start collector
sudo systemctl start elled-ebpfd

# Start daemon (will connect to socket)
sudo systemctl start elled

# Trigger event and verify in DB
sudo stress --vm 1 --vm-bytes 95% --timeout 5s
elle events --category oom --since 1m
```

## Security

The collector runs with minimal capabilities:
- `CAP_BPF` - Load and manage BPF programs
- `CAP_PERFMON` - Access performance events
- `CAP_SYS_RESOURCE` - Set memory limits for BPF
- `CAP_NET_ADMIN` - Network tracing

Systemd hardening includes:
- `ProtectSystem=strict` - Read-only filesystem
- `ProtectHome=true` - No access to home directories
- `PrivateTmp=true` - Isolated /tmp
- `NoNewPrivileges=true` - No privilege escalation
- `MemoryMax=64M` - Memory limit

## Fallback Behavior

The Python daemon (`elled`) automatically detects which backend to use:

1. If `/run/elle/telemetry.sock` exists → Use C collector (preferred)
2. Otherwise → Fall back to BCC-Python implementation

This ensures backward compatibility while preferring the faster C implementation.
