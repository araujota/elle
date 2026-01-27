# ELLE Telemetry Documentation

This document outlines all telemetry signals captured by `elled-telemetryd` (the C daemon) and the data fields stored in incident reports.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        elled-telemetryd (C Daemon)                          │
│                                                                             │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐   │
│  │   Journal   │ │   Docker    │ │   Inotify   │ │     eBPF Probes     │   │
│  │   Watcher   │ │   Watcher   │ │   Watcher   │ │  (kernel tracing)   │   │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────────────┘   │
│                                                                             │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐   │
│  │  PSI Probe  │ │ Memory Probe│ │ Disk Probe  │ │   Network Probe     │   │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────────────┘   │
│                                                                             │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐                           │
│  │Thermal Probe│ │Package Probe│ │  Port Probe │                           │
│  └─────────────┘ └─────────────┘ └─────────────┘                           │
│                           │                                                 │
│                           ▼                                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                     C Normalizer (PCRE2)                             │   │
│  │  • 40+ category detection patterns                                   │   │
│  │  • 23 entity extraction patterns                                     │   │
│  │  • SHA256 fingerprinting                                             │   │
│  │  • 60-second deduplication window                                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                           │                                                 │
│                           ▼                                                 │
│                  /run/elle/telemetry.sock                                   │
│                     (NDJSON output)                                         │
└─────────────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         elled (Python Daemon)                               │
│                                                                             │
│  TelemetrydWatcher → TelemetryEvent → IncidentCorrelator → IncidentReport  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Event Sources

### 1.1 Journal Watcher

**Source:** `sd-journal` API (libsystemd)

Monitors all systemd journal entries in real-time using cursor-based following.

| Signal | Description |
|--------|-------------|
| All journald entries | Service logs, syslog, stdout/stderr |
| Kernel messages | When `_TRANSPORT=kernel` filter applied |
| Priority levels | 0 (emerg) through 7 (debug) |

**Key journald fields captured:**
- `MESSAGE` - Log message content
- `PRIORITY` - Syslog priority (0-7)
- `SYSLOG_IDENTIFIER` - Syslog identifier
- `_SYSTEMD_UNIT` - Systemd unit name
- `_COMM` - Command name
- `_EXE` - Executable path
- `_TRANSPORT` - Transport type (journal, stdout, kernel)
- `__REALTIME_TIMESTAMP` - Timestamp in microseconds

---

### 1.2 Docker Watcher

**Source:** `docker events --format json` subprocess

Monitors Docker container lifecycle events with crashloop detection.

| Signal | Description |
|--------|-------------|
| `start` | Container started |
| `stop` | Container stopped |
| `die` | Container exited |
| `restart` | Container restarted |
| `kill` | Container killed |
| `oom` | Container OOM killed |
| `crashloop` | Synthetic event: ≥3 restarts in 5 minutes |

**Fields captured:**
- `name` - Container name
- `id` - Container ID (short, 12 chars)
- `action` - Event action
- `image` - Container image
- `status` - Container status

**Crashloop detection logic:**
1. Track restart timestamps per container in ring buffer
2. On restart/start after die: record timestamp
3. Clean entries older than 300 seconds
4. If count ≥ 3: emit crashloop event, reset count

---

### 1.3 Inotify Watcher

**Source:** Linux inotify API

Monitors security-sensitive files for changes with diff generation.

| Event | Description |
|-------|-------------|
| `IN_MODIFY` | File content modified |
| `IN_CREATE` | File created |
| `IN_DELETE` | File deleted |
| `IN_ATTRIB` | File attributes changed (permissions, owner) |

**Default watched paths:**
```
/root/.ssh/authorized_keys
/home/*/.ssh/authorized_keys
/etc/ssh/sshd_config
/etc/sudoers
/etc/passwd
/etc/shadow
```

**Fields captured:**
- `path` - File path
- `event` - Event type
- `diff` - Unified diff (for text files)
- `mtime` - Modification time

---

### 1.4 eBPF Probes

**Source:** BPF programs attached to kernel tracepoints

#### 1.4.1 OOM Kill Tracing

**Tracepoint:** `oom:mark_victim`

Captures when the OOM killer selects a victim process.

| Field | Type | Description |
|-------|------|-------------|
| `pid` | u32 | Victim process ID |
| `tgid` | u32 | Thread group ID |
| `uid` | u32 | User ID |
| `oom_score_adj` | s16 | OOM score adjustment |
| `comm` | string | Process command name |
| `total_vm` | u64 | Total virtual memory (pages) |
| `anon_rss` | u64 | Anonymous RSS (pages) |
| `file_rss` | u64 | File-backed RSS (pages) |
| `shmem_rss` | u64 | Shared memory RSS (pages) |

---

#### 1.4.2 Process Execution Tracing

**Tracepoint:** `syscalls:sys_enter_execve`, `syscalls:sys_exit_execve`

Tracks process execution with enter/exit correlation.

| Field | Type | Description |
|-------|------|-------------|
| `pid` | u32 | Process ID |
| `ppid` | u32 | Parent process ID |
| `uid` | u32 | User ID |
| `filename` | string | Executed file path |
| `exit_code` | s32 | Execution result |
| `latency_ns` | u64 | Execution latency |

---

#### 1.4.3 Block I/O Latency Tracing

**Tracepoint:** `block:block_rq_issue`, `block:block_rq_complete`

Tracks block I/O request latency and errors.

| Field | Type | Description |
|-------|------|-------------|
| `dev` | u32 | Device major:minor |
| `sector` | u64 | Starting sector |
| `nr_sector` | u32 | Number of sectors |
| `bytes` | u32 | Request size in bytes |
| `latency_ns` | u64 | I/O latency in nanoseconds |
| `rwbs` | string | R/W/B/S flags |
| `error` | s32 | Error code (0 = success) |

---

#### 1.4.4 TCP Retransmission Tracing

**Tracepoint:** `tcp:tcp_retransmit_skb`

Detects TCP retransmissions indicating network issues.

| Field | Type | Description |
|-------|------|-------------|
| `saddr` | u32 | Source IPv4 address |
| `daddr` | u32 | Destination IPv4 address |
| `sport` | u16 | Source port |
| `dport` | u16 | Destination port |
| `state` | u8 | TCP connection state |

---

#### 1.4.5 Capability Denial Tracing

**Tracepoint:** `capability:cap_capable`

Traces capability check failures to diagnose permission issues.

| Field | Type | Description |
|-------|------|-------------|
| `pid` | u32 | Process ID |
| `tgid` | u32 | Thread group ID |
| `uid` | u32 | User ID |
| `cap` | s32 | Capability number |
| `cap_name` | string | Capability name (e.g., `CAP_NET_BIND_SERVICE`) |
| `comm` | string | Process command name |

**Tracked capabilities:**
- `CAP_CHOWN`, `CAP_DAC_OVERRIDE`, `CAP_DAC_READ_SEARCH`
- `CAP_NET_BIND_SERVICE`, `CAP_NET_ADMIN`, `CAP_NET_RAW`
- `CAP_SYS_ADMIN`, `CAP_SYS_PTRACE`, `CAP_SYS_MODULE`
- And 12 others

---

#### 1.4.6 File Permission Denial Tracing

**Tracepoint:** `syscalls:sys_enter_openat`, `syscalls:sys_exit_openat`

Traces file access denials (EACCES, EPERM, EROFS).

| Field | Type | Description |
|-------|------|-------------|
| `pid` | u32 | Process ID |
| `uid` | u32 | User ID |
| `filename` | string | Attempted file path |
| `flags` | u32 | Open flags |
| `error` | s32 | Error code (1=EPERM, 13=EACCES, 30=EROFS) |
| `comm` | string | Process command name |

---

#### 1.4.7 Mount Operation Tracing

**Tracepoint:** `syscalls:sys_enter_mount`, `syscalls:sys_exit_mount`

Traces mount operations, especially remount-ro (filesystem error indicator).

| Field | Type | Description |
|-------|------|-------------|
| `source` | string | Source device |
| `target` | string | Mount target path |
| `fstype` | string | Filesystem type |
| `flags` | u32 | Mount flags |
| `error` | s32 | Error code |
| `is_remount_ro` | bool | True if remount read-only |

---

### 1.5 Periodic Probes

#### 1.5.1 PSI Probe (Pressure Stall Information)

**Source:** `/proc/pressure/cpu`, `/proc/pressure/memory`, `/proc/pressure/io`

| Metric | Description |
|--------|-------------|
| `cpu_some_avg10` | CPU pressure (some) 10s average |
| `cpu_some_avg60` | CPU pressure (some) 60s average |
| `cpu_some_avg300` | CPU pressure (some) 5min average |
| `memory_some_avg10` | Memory pressure (some) 10s average |
| `memory_full_avg10` | Memory pressure (full) 10s average |
| `io_some_avg10` | I/O pressure (some) 10s average |
| `io_full_avg10` | I/O pressure (full) 10s average |

---

#### 1.5.2 Memory Probe

**Source:** `/proc/meminfo`

| Metric | Description |
|--------|-------------|
| `mem_total_mb` | Total memory in MB |
| `mem_available_mb` | Available memory in MB |
| `mem_free_mb` | Free memory in MB |
| `mem_pressure` | Pressure ratio (1 - available/total) |
| `swap_total_mb` | Total swap in MB |
| `swap_free_mb` | Free swap in MB |
| `swap_pressure` | Swap usage ratio |

**Thresholds:**
- Warning: 85% memory pressure
- Critical: 95% memory pressure
- Swap warning: 80% swap used

---

#### 1.5.3 Disk Probe

**Source:** `/proc/mounts` + `statvfs()`

| Metric | Description |
|--------|-------------|
| `mount` | Mount point path |
| `device` | Block device |
| `fstype` | Filesystem type |
| `total_gb` | Total size in GB |
| `avail_gb` | Available size in GB |
| `used_pct` | Usage percentage |

**Excluded filesystems:** squashfs, tmpfs, devtmpfs, overlay, cgroup, proc, sysfs

**Threshold:** Warning at 90% usage

---

#### 1.5.4 Network Probe

**Source:** `/sys/class/net/*/operstate`, `/sys/class/net/*/statistics/*`

| Metric | Description |
|--------|-------------|
| `interface` | Interface name |
| `operstate` | Operational state (up/down) |
| `rx_bytes` | Received bytes |
| `tx_bytes` | Transmitted bytes |
| `rx_errors` | Receive errors |
| `tx_errors` | Transmit errors |
| `rx_dropped` | Dropped received packets |
| `tx_dropped` | Dropped transmitted packets |

**Alert triggers:**
- Interface state change to "down"
- Delta errors > 10 since last probe

---

#### 1.5.5 Thermal Probe

**Source:** `/sys/class/thermal/thermal_zone*/temp`, `/sys/class/thermal/thermal_zone*/type`

| Metric | Description |
|--------|-------------|
| `zone` | Thermal zone name |
| `type` | Zone type (e.g., x86_pkg_temp) |
| `temp_celsius` | Temperature in Celsius |

**Threshold:** Warning at 80°C

---

#### 1.5.6 Package Probe

**Source:** `/var/lib/dpkg/status`

| Event | Description |
|-------|-------------|
| `upgrade` | Package version changed |
| `install` | New package installed |
| `remove` | Package removed |

**Fields captured:**
- `package` - Package name
- `old_version` - Previous version (for upgrades)
- `new_version` - Current version

**Optimization:** Checks `stat()` mtime before re-parsing

---

#### 1.5.7 Port Probe

**Source:** `/proc/net/tcp`, `/proc/net/tcp6`, `/proc/net/udp`, `/proc/net/udp6`

| Metric | Description |
|--------|-------------|
| `port` | Listening port |
| `proto` | Protocol (TCP/UDP) |
| `inode` | Socket inode |
| `pid` | Process ID (via /proc/*/fd lookup) |
| `comm` | Process name |

**Sensitive ports monitored:**
| Port | Name | Trusted Processes |
|------|------|-------------------|
| 22 | ssh | sshd, dropbear |
| 80 | http | nginx, apache2, httpd, caddy |
| 443 | https | nginx, apache2, httpd, caddy |
| 3306 | mysql | mysqld, mariadbd |
| 5432 | postgres | postgres, postmaster |
| 6379 | redis | redis-server |
| 27017 | mongodb | mongod |

---

## 2. Category Detection Patterns

The C normalizer uses PCRE2 compiled regex patterns to categorize events.

### 2.1 Kernel Panic (8 patterns)
```
Kernel panic\s*-?\s*not syncing
\bBUG:\s
kernel BUG at
Oops:\s
general protection fault
divide error
invalid opcode
stack overflow
```

### 2.2 OOM Events (2 patterns)
```
Out of memory
oom-kill
```

### 2.3 Disk Events (2 patterns)
```
I/O error
disk quota exceeded
```

### 2.4 SMART Events (1 pattern)
```
SMART.*error|media.*error
```

### 2.5 Filesystem Events (2 patterns)
```
EXT4-fs error
Remounting filesystem read-only
```

### 2.6 Network Events (10 patterns)
```
link.*(?:down|up)
connection refused
connection timed out
no route to host
network.*unreachable
DNS.*(?:fail|timeout)
RTNETLINK.*error
address already in use
TCP.*retransmit
socket.*error
```

### 2.7 Authentication Events (7 patterns)
```
authentication fail
permission denied
access denied
invalid user
Failed password
pam_unix.*authentication failure
sudo.*incorrect password
```

### 2.8 Service Events (4 patterns)
```
(?:start|stop|restart)(?:ing|ed)\s+\S+\.service
(?:Failed|Succeeded)\s+to\s+start
systemd.*(?:Starting|Started|Stopped|Failed)
Unit.*entered failed state
```

### 2.9 Thermal Events (2 patterns)
```
temperature.*(?:critical|above|exceeded)
thermal.*(?:throttl|shutdown)
```

### 2.10 Package Events (3 patterns)
```
(?:install|remove|upgrade|dpkg).*package
apt(?:-get)?\s+(?:install|remove|upgrade)
Package.*(?:installed|removed|upgraded)
```

### 2.11 Docker Events (4 patterns)
```
container.*(?:start|stop|die|kill|oom)
docker.*(?:start|stop|restart)
crashloop.*detected
container.*exited
```

---

## 3. Entity Extraction Patterns

The normalizer extracts entities from messages using 23 patterns.

| Entity Type | Pattern Example | Extracted Value |
|-------------|-----------------|-----------------|
| `cpu:N` | `CPU 3 stuck` | `cpu:3` |
| `module:X` | `module foo loaded` | `module:foo` |
| `function:X` | `BUG at function bar` | `function:bar` |
| `service:X` | `nginx.service started` | `service:nginx` |
| `interface:X` | `eth0 link down` | `interface:eth0` |
| `disk:X` | `error on sda` | `disk:sda` |
| `disk:X` | `error on nvme0n1` | `disk:nvme0n1` |
| `container:X` | `container abc123 died` | `container:abc123` |
| `mount:X` | `mount /home failed` | `mount:/home` |
| `user:X` | `user john failed login` | `user:john` |
| `pid:N` | `killed process 1234` | `pid:1234` |
| `port:N` | `port 8080 in use` | `port:8080` |
| `file:X` | `error reading /etc/foo` | `file:/etc/foo` |
| `wg:X` | `wg0 handshake failed` | `wg:wg0` |

---

## 4. TelemetryEvent Fields

Events emitted from telemetryd via Unix socket in NDJSON format.

```json
{
  "ts": 1706300000000000000,
  "source": "journal|kernel|docker|inotify|probe|ebpf",
  "severity": "debug|info|notice|warning|error|critical",
  "category": "oom|disk|net|auth|service|docker|fs|pkg|thermal|smart|kernel_panic|other",
  "message": "Human-readable event description",
  "entity": "service:nginx|interface:eth0|container:abc123|null",
  "fingerprint": "sha256_16char_hex",
  "raw": { ... source-specific fields ... }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | string | Unique 12-char hex identifier |
| `ts` | datetime | Event timestamp |
| `source` | enum | Event source: journal, kernel, probe, ebpf |
| `severity` | enum | debug, info, notice, warning, error, critical |
| `category` | string | Detected category (see patterns above) |
| `message` | string | Human-readable message |
| `entity` | string? | Affected entity (e.g., `service:nginx`) |
| `fingerprint` | string | SHA256[:16] for deduplication |
| `raw` | dict | Source-specific raw data |

---

## 5. Incident Report Fields

### 5.1 IncidentReport

| Field | Type | Description |
|-------|------|-------------|
| `incident_id` | string | UUID for this incident |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |
| `domain` | enum | net, disk, oom, docker, auth, pkg, fs, service, gui, other |
| `severity` | enum | info, warning, error, critical |
| `status` | enum | open, mitigated, resolved, false_positive |
| `title` | string | Brief title |
| `summary` | string | 2-5 sentence summary |
| `symptoms` | tuple[str] | Human-readable symptom bullets |
| `suspected_causes` | tuple[str] | Hypotheses about the cause |
| `root_cause` | string? | Confirmed root cause |
| `event_ids` | tuple[str] | Linked telemetry event IDs |
| `log_snippets` | tuple[str] | Relevant log excerpts |
| `metrics` | dict | Key metrics: disk%, temp, SMART, etc. |
| `decision` | dict | Chosen plan and rationale |
| `preconditions` | tuple[Precondition] | Conditions for solution to apply |
| `outcome` | enum | unknown, improved, partial, no_change, worse |
| `verification_steps` | tuple[str] | Verification checks run |
| `time_to_mitigate_sec` | int? | Seconds to mitigation |
| `time_to_resolve_sec` | int? | Seconds to resolution |
| `fingerprint` | Fingerprint | Features for matching |
| `tags` | tuple[str] | User-defined tags |
| `confidence` | float | Confidence in diagnosis (0.0-1.0) |
| `trigger_source` | enum | telemetry, command_failure, user_task, manual, gui_automation |
| `trigger_command` | string? | Command that triggered (if command_failure) |
| `decision_record` | DecisionRecord? | Structured decision with provenance |
| `config_states` | tuple[ConfigFileState] | Config files modified during resolution |

### 5.2 SystemSnapshot

Captured at incident start and after resolution. This is a "maximum-surface snapshot" designed to capture all relevant system state at the moment of an incident.

| Field | Type | Description |
|-------|------|-------------|
| `os` | string | OS name and version (e.g., "Ubuntu 24.04") |
| `kernel` | string | Kernel version |
| `uptime_sec` | int | System uptime in seconds |
| `hostname` | string | System hostname |
| `cpu_load` | tuple[float, float, float] | Load averages: 1min, 5min, 15min |
| `mem_total_mb` | int | Total memory in MB |
| `mem_free_mb` | int | Free memory in MB |
| `mem_available_mb` | int | Available memory in MB |
| `swap_total_mb` | int | Total swap in MB |
| `swap_used_mb` | int | Used swap in MB |
| `disks` | tuple[dict] | Disk info: mount, used_pct, avail_gb, device |
| `interfaces` | tuple[dict] | Network interfaces: name, state, rx_err, tx_err, ip |
| `services` | tuple[dict] | Systemd services: name, active, failed |
| `docker_running` | int | Running containers count |
| `docker_exited` | int | Exited containers count |
| `docker_containers` | tuple[dict] | Container details: name, state, image |
| `temps` | tuple[dict] | Temperature sensors: sensor, celsius |
| `smart` | tuple[dict] | SMART info: dev, health, pct_used, media_errors |
| `packages` | tuple[PackageState] | Bedrock + domain-specific + context-relevant package versions |
| `kernel_modules` | tuple[dict] | Loaded kernel modules: name, size |
| `docker_images` | tuple[dict] | Docker images for running containers: image, tag, digest |
| `recent_apt_history` | tuple[dict] | Apt operations in last 24h: action, packages, timestamp |
| `collected_at` | datetime | Snapshot collection timestamp |

#### Package Collection Strategy

The snapshot collector captures three tiers of packages:

1. **Bedrock packages (~30):** Always captured - kernel, systemd, libc, Python, OpenSSL, networking, security, container runtime, observability tools

2. **Domain-specific packages:** Based on incident domain:
   - `docker`: docker.io, containerd, runc, docker-compose
   - `net`: iproute2, iptables, nftables, ufw, wireguard, nginx, apache2
   - `auth`: libpam-modules, openssh-server, sssd, sudo
   - `disk`: lvm2, mdadm, cryptsetup, e2fsprogs, nfs-common
   - `oom`: earlyoom, systemd-oomd
   - `service`: systemd, dbus

3. **Context-relevant packages:** Extracted from command, error output, and entity name

4. **Optional pip packages:** Key Python libraries (requests, sqlalchemy, flask, etc.)

### 5.3 Fingerprint

Derived features for incident similarity matching.

| Field | Type | Description |
|-------|------|-------------|
| `disk_pressure` | float | Max disk usage ratio (0.0-1.0) |
| `mem_pressure` | float | Memory pressure (0.0-1.0) |
| `swap_pressure` | float | Swap usage ratio (0.0-1.0) |
| `cpu_pressure` | float | CPU load (1min average) |
| `oom_count_1h` | int | OOM kills in last hour |
| `net_flaps_1h` | int | Network state changes in last hour |
| `service_failures_1h` | int | Service failures in last hour |
| `auth_failures_1h` | int | Auth failures in last hour |
| `entities` | tuple[str] | Involved entities |
| `smart_pct_used_max` | int | Max NVMe/SSD percentage used |
| `smart_media_errors` | int | SMART media error count |
| `temp_max_c` | int | Max temperature in Celsius |
| `docker_exited_count` | int | Exited container count |
| `custom` | dict | Extensible custom features |

### 5.4 IncidentAction

Actions taken during incident handling.

| Field | Type | Description |
|-------|------|-------------|
| `id` | int? | Database ID |
| `incident_id` | string | Parent incident UUID |
| `step_index` | int | Order in action sequence |
| `kind` | enum | shell, edit, privileged, verify, rollback, capability, gui |
| `command` | string? | Shell command if applicable |
| `payload` | dict | Action-specific data |
| `exit_code` | int? | Command exit code |
| `stdout` | string? | Command stdout (truncated) |
| `stderr` | string? | Command stderr (truncated) |
| `success` | bool | Whether action succeeded |
| `privileged` | bool | Required elevated privileges |
| `syscall_summary` | SyscallSummary? | Syscall-level trace |
| `created_at` | datetime | Action timestamp |
| `duration_ms` | int? | Execution duration |

### 5.5 Provenance

Tracks sources that informed a decision.

| Field | Type | Description |
|-------|------|-------------|
| `man_pages` | tuple[ManPageCitation] | Man page citations |
| `prior_incidents` | tuple[IncidentCitation] | Prior incident citations |
| `triggering_events` | TelemetryCitation? | Telemetry events that triggered incident |
| `primary_source` | enum | man_vault, incident_vault, telemetry, llm_only |

---

## 6. Example Incident Report

The following example shows a complete incident report with a full system snapshot demonstrating the "maximum-surface" approach to capturing system state.

```json
{
  "incident_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "created_at": "2024-01-26T15:30:00Z",
  "updated_at": "2024-01-26T15:45:00Z",
  "domain": "disk",
  "severity": "warning",
  "status": "resolved",
  "title": "Disk space critical on /var",
  "summary": "The /var partition reached 95% usage, primarily due to accumulated Docker container logs. Cleaned up old logs and configured log rotation to prevent recurrence.",
  "symptoms": [
    "Disk usage on /var at 95%",
    "Docker logs consuming 15GB",
    "System unable to write new log entries"
  ],
  "suspected_causes": [
    "Docker container logging without rotation",
    "Large application log files",
    "Orphaned temporary files"
  ],
  "root_cause": "Docker container logs for nginx-proxy accumulated without rotation",
  "event_ids": [
    "a1b2c3d4e5f6",
    "b2c3d4e5f6a1",
    "c3d4e5f6a1b2"
  ],
  "log_snippets": [
    "Jan 26 15:25:12 server kernel: EXT4-fs warning: /dev/sda1: no space left on device",
    "Jan 26 15:25:15 server docker: Error writing log: no space left on device"
  ],
  "metrics": {
    "disk_used_pct": 95.2,
    "docker_logs_gb": 15.3,
    "var_avail_gb": 2.1
  },
  "preconditions": [
    {
      "expression": "disk./var.used_pct > 90",
      "description": "/var partition usage above 90%",
      "required": true
    }
  ],
  "outcome": "improved",
  "verification_steps": [
    "df -h /var shows 65% usage",
    "Docker log rotation configured",
    "Test write to /var succeeded"
  ],
  "time_to_mitigate_sec": 300,
  "time_to_resolve_sec": 900,
  "fingerprint": {
    "disk_pressure": 0.95,
    "mem_pressure": 0.45,
    "swap_pressure": 0.10,
    "cpu_pressure": 1.2,
    "oom_count_1h": 0,
    "net_flaps_1h": 0,
    "service_failures_1h": 2,
    "auth_failures_1h": 0,
    "entities": ["disk:sda1", "mount:/var", "container:nginx-proxy"],
    "smart_pct_used_max": 45,
    "smart_media_errors": 0,
    "temp_max_c": 52,
    "docker_exited_count": 3,
    "custom": {}
  },
  "tags": ["docker", "logging", "disk-full"],
  "confidence": 0.92,
  "trigger_source": "telemetry",
  "trigger_command": null,
  "decision_record": {
    "chosen_approach": "Clean Docker logs and configure log rotation",
    "rationale": "Docker logs are the primary space consumer. Cleaning them frees immediate space, and rotation prevents recurrence.",
    "confidence": {
      "overall": 0.92,
      "from_man_vault": 0.15,
      "from_incident_vault": 0.45,
      "from_telemetry": 0.25,
      "from_llm": 0.07,
      "dominant_tier": "fingerprint"
    },
    "provenance": {
      "man_pages": [
        {
          "name": "docker",
          "section": "1",
          "snippet": "docker system prune - Remove unused data",
          "match_section": "COMMANDS",
          "relevance_score": 0.85
        }
      ],
      "prior_incidents": [
        {
          "incident_id": "prev-incident-uuid",
          "title": "Docker logs filling /var",
          "outcome": "improved",
          "similarity_score": 0.89,
          "match_type": "fingerprint",
          "successful_actions": [
            "docker system prune -f",
            "truncate -s 0 /var/lib/docker/containers/*/*-json.log"
          ]
        }
      ],
      "triggering_events": {
        "event_ids": ["a1b2c3d4e5f6", "b2c3d4e5f6a1"],
        "event_summaries": [
          "Disk usage /var at 95%",
          "Docker log write failed: no space"
        ],
        "trigger_time": "2024-01-26T15:25:00Z"
      },
      "primary_source": "incident_vault"
    },
    "planned_commands": [
      "truncate -s 0 /var/lib/docker/containers/*/*-json.log",
      "docker system prune -f",
      "cat > /etc/docker/daemon.json << 'EOF'\n{\"log-driver\": \"json-file\", \"log-opts\": {\"max-size\": \"10m\", \"max-file\": \"3\"}}\nEOF",
      "systemctl reload docker"
    ],
    "decided_at": "2024-01-26T15:32:00Z"
  },
  "config_states": [
    {
      "path": "/etc/docker/daemon.json",
      "sha256_before": null,
      "sha256_after": "a1b2c3d4e5f6789012345678901234567890abcdef",
      "size_bytes": 89,
      "mtime": "2024-01-26T15:40:00Z",
      "backup_path": null
    }
  ]
}
```

### 6.1 Example SystemSnapshot (Pre-Incident)

This shows the full system state captured when the incident was detected:

```json
{
  "os": "Ubuntu 24.04",
  "kernel": "6.8.0-45-generic",
  "uptime_sec": 864000,
  "hostname": "prod-server-01",
  "cpu_load": [1.2, 0.95, 0.78],
  "mem_total_mb": 16384,
  "mem_free_mb": 512,
  "mem_available_mb": 8960,
  "swap_total_mb": 4096,
  "swap_used_mb": 410,
  "disks": [
    {"mount": "/", "used_pct": 45, "avail_gb": 55.2, "device": "/dev/sda2"},
    {"mount": "/var", "used_pct": 95, "avail_gb": 2.1, "device": "/dev/sda1"},
    {"mount": "/home", "used_pct": 62, "avail_gb": 180.5, "device": "/dev/sdb1"}
  ],
  "interfaces": [
    {"name": "eth0", "state": "UP", "rx_err": 0, "tx_err": 0},
    {"name": "docker0", "state": "UP", "rx_err": 0, "tx_err": 0}
  ],
  "services": [
    {"name": "docker", "active": true, "failed": false},
    {"name": "sshd", "active": true, "failed": false},
    {"name": "nginx", "active": true, "failed": false}
  ],
  "docker_running": 5,
  "docker_exited": 3,
  "docker_containers": [
    {"name": "nginx-proxy", "state": "running", "image": "nginx:1.25"},
    {"name": "postgres-db", "state": "running", "image": "postgres:16"},
    {"name": "redis-cache", "state": "running", "image": "redis:7-alpine"},
    {"name": "app-backend", "state": "running", "image": "myapp:v2.3.1"},
    {"name": "app-worker", "state": "running", "image": "myapp:v2.3.1"}
  ],
  "temps": [
    {"sensor": "coretemp/Core 0", "celsius": 52},
    {"sensor": "coretemp/Core 1", "celsius": 48},
    {"sensor": "nvme/Composite", "celsius": 38}
  ],
  "smart": [
    {"dev": "/dev/nvme0n1", "health": "PASSED", "pct_used": 12, "media_errors": 0},
    {"dev": "/dev/sda", "health": "PASSED", "pct_used": 0, "media_errors": 0}
  ],
  "packages": [
    {"name": "linux-image-generic", "version": "6.8.0-45.45", "source": "apt", "is_bedrock": true},
    {"name": "systemd", "version": "255.4-1ubuntu8", "source": "apt", "is_bedrock": true},
    {"name": "libc6", "version": "2.39-0ubuntu8", "source": "apt", "is_bedrock": true},
    {"name": "python3", "version": "3.12.3-0ubuntu1", "source": "apt", "is_bedrock": true},
    {"name": "openssl", "version": "3.0.13-0ubuntu3", "source": "apt", "is_bedrock": true},
    {"name": "docker.io", "version": "24.0.7-0ubuntu2", "source": "apt", "is_bedrock": true},
    {"name": "containerd", "version": "1.7.12-0ubuntu2", "source": "apt", "is_bedrock": true},
    {"name": "runc", "version": "1.1.12-0ubuntu2", "source": "apt", "is_bedrock": true},
    {"name": "e2fsprogs", "version": "1.47.0-2.4ubuntu1", "source": "apt", "is_bedrock": false},
    {"name": "nginx", "version": "1.24.0-2ubuntu1", "source": "apt", "is_bedrock": false}
  ],
  "kernel_modules": [
    {"name": "ext4", "size": "1015808"},
    {"name": "overlay", "size": "180224"},
    {"name": "br_netfilter", "size": "32768"},
    {"name": "nf_conntrack", "size": "188416"},
    {"name": "nvme", "size": "57344"},
    {"name": "nvme_core", "size": "135168"}
  ],
  "docker_images": [
    {"image": "nginx", "tag": "1.25", "digest": "sha256:a1b2c3d4e5f67890"},
    {"image": "postgres", "tag": "16", "digest": "sha256:b2c3d4e5f67890a1"},
    {"image": "redis", "tag": "7-alpine", "digest": "sha256:c3d4e5f67890a1b2"},
    {"image": "myapp", "tag": "v2.3.1", "digest": "sha256:d4e5f67890a1b2c3"}
  ],
  "recent_apt_history": [
    {
      "timestamp": "2024-01-25T10:15:00",
      "action": "upgrade",
      "packages": "docker.io:amd64 (24.0.5-0ubuntu1, 24.0.7-0ubuntu2)"
    },
    {
      "timestamp": "2024-01-24T08:30:00",
      "action": "install",
      "packages": "nginx:amd64 (1.24.0-2ubuntu1)"
    }
  ],
  "collected_at": "2024-01-26T15:30:00Z"
}
```

This snapshot captures:
- **30+ bedrock packages** (kernel, systemd, libc, openssl, docker runtime, etc.)
- **Domain-specific packages** (e2fsprogs for disk domain)
- **Context-relevant packages** (nginx extracted from entity)
- **Kernel modules** (ext4, overlay, nvme - relevant to disk issues)
- **Docker image versions** (with digests for reproducibility)
- **Recent apt history** (docker upgrade 1 day before incident)

---

## 7. Socket Protocol

The daemon communicates via Unix socket at `/run/elle/telemetry.sock`.

**Format:** NDJSON (newline-delimited JSON)

**Connection:** Stream socket, multiple concurrent clients supported

**Example stream:**
```json
{"ts":1706300000000000000,"source":"journal","severity":"info","category":"service","message":"Started nginx.service","entity":"service:nginx","fingerprint":"a1b2c3d4e5f6a1b2","raw":{"_SYSTEMD_UNIT":"nginx.service"}}
{"ts":1706300001000000000,"source":"ebpf","severity":"warning","category":"oom","message":"OOM kill: python3 (pid 1234)","entity":"pid:1234","fingerprint":"b2c3d4e5f6a1b2c3","raw":{"pid":1234,"uid":1000,"oom_score_adj":0}}
{"ts":1706300002000000000,"source":"probe","severity":"warning","category":"disk","message":"Disk /var at 92%","entity":"mount:/var","fingerprint":"c3d4e5f6a1b2c3d4","raw":{"mount":"/var","used_pct":92.1}}
```
